"""The fantasy-football tools an agent can call. Every number comes from the database or a
deterministic service; tools never make up values.

Players can be passed by name (case-insensitive, typo-tolerant) or numeric id. Handlers live here
(generic tools) and in tools/league_tools.py (the user's Yahoo leagues); names, descriptions and
schemas are in tools/tool_specs.py. Rows are compact (tools/rows.py) so results fit a free-tier budget.
"""
from typing import Any

from sqlalchemy.orm import Session

from config import get_settings
from integrations.sleeper import SleeperError, trending_players
from services.experts import expert_rankings
from services.flags import player_flags
from services.game_locks import kickoffs
from services.insights import find_insights
from services.labels import label
from services.leaderboards import catalog, leaderboard
from services.players import AmbiguousPlayer, PlayerNotFound, player_profile, resolve_player, search_players
from services.projections import available_seasons, latest_season
from services.valuation import NoDataError, build_valuation
from services.trade_finder import find_trades
from services.waivers import waiver_candidates
from services.weekly_rankings import build_weekly_rankings, default_week
from services import serialize
from tools import league_tools, trade_tools
from tools.injury_tools import INJURY_REPORT
from tools.league_tools import TRADE_PERSPECTIVE, settings_for
from tools.registry import Tool, ToolError, ToolRegistry, ToolUnavailable
from tools.rows import header, matchup_words, player_str, schedule_words, weekly_row
from tools.start_sit_tool import START_SIT
from tools.tool_specs import TOOL_SPECS

__all__ = ["TOOLS", "TOOL_ERRORS", "sanitize_for_agent", "schedule_words", "TRADE_PERSPECTIVE"]

PROFILE_GAMES_SHOWN = 5
PFR_REASON_PREFIX = "Snap share"
EXPERTS_PER_POSITION = 10
SCHEDULE_TEAMS_SHOWN = 6           # easiest and hardest remaining schedules when no team is named
KEEP_ZERO = {"week", "fantasy_points", "expected_points", "season_type", "opponent"}

# Errors whose messages are written for the user/model. Anything else is a bug: log it, and
# show only a generic failure. (Bare ValueError is not here: library messages can echo input.)
TOOL_ERRORS = (ToolError, ToolUnavailable, PlayerNotFound, AmbiguousPlayer, NoDataError)


def _checked_season(session: Session, season: int | None) -> int | None:
    if season is not None and season not in available_seasons(session):
        raise ToolError(f"No data loaded for season {season}. Available: {available_seasons(session)}.")
    return season


def _service(call, *args, **kwargs):
    """Run a service whose ValueErrors are our own validation messages, as ToolErrors."""
    try:
        return call(*args, **kwargs)
    except ValueError as exc:
        raise ToolError(str(exc)[:300]) from exc


def sanitize_for_agent(value: Any) -> Any:
    """Drop PFR-sourced snap data from tool output unless the config opts in."""
    if get_settings().agent_share_pfr_data:
        return value
    if isinstance(value, dict):
        return {k: sanitize_for_agent(v) for k, v in value.items() if k != "snap_pct"}
    if isinstance(value, list):
        return [sanitize_for_agent(v) for v in value
                if not (isinstance(v, str) and v.startswith(PFR_REASON_PREFIX))]
    return value


def _game(row: dict) -> dict:
    """A box-score row without the zero stats that don't apply to his position."""
    return {k: v for k, v in row.items() if v not in (0, 0.0, None) or k in KEEP_ZERO}


def _search(session: Session, query: str, position: str | None = None, limit: int = 10) -> dict:
    return {"results": [serialize.player_ref(r) for r in search_players(session, query, position, limit)]}


def _profile(session: Session, player: str, season: int | None = None, league: str | None = None,
             **fmt: Any) -> dict:
    settings, _ = settings_for(session, league, **fmt)
    profile = player_profile(session, resolve_player(session, player).id, settings, _checked_season(session, season))
    ref = profile["player"]
    return {**profile, "player": player_str(ref["name"], ref.get("position"), ref.get("team")),
            "game_log": [_game(g) for g in profile["game_log"][-PROFILE_GAMES_SHOWN:]],
            "note": f"game_log shows the last {PROFILE_GAMES_SHOWN} games; use get_player_game_log for all."}


def _game_log(session: Session, player: str, season: int | None = None, last_n: int | None = None,
              scoring: str | None = None, league: str | None = None) -> dict:
    settings, _ = settings_for(session, league, scoring=scoring)
    profile = player_profile(session, resolve_player(session, player).id, settings, _checked_season(session, season))
    games = profile["game_log"][-last_n:] if last_n else profile["game_log"]
    ref = profile["player"]
    return {"player": player_str(ref["name"], ref.get("position"), ref.get("team")), "season": profile["season"],
            "scoring": settings.scoring, "games": [_game(g) for g in games]}


def _compare(session: Session, players: list[str], season: int | None = None, league: str | None = None,
             **fmt: Any) -> dict:
    settings, _ = settings_for(session, league, **fmt)
    season = _checked_season(session, season)
    profiles = [player_profile(session, resolve_player(session, name).id, settings, season) for name in players]
    return {"season": profiles[0]["season"], "scoring": settings.scoring, "players": [
        {"player": player_str(p["player"]["name"], p["player"].get("position"), p["player"].get("team")),
         **{key: p[key] for key in ("summary", "projection", "signals")}} for p in profiles]}


def _ros_row(value, ecr: dict, rank: int) -> dict:
    row = {"rank": rank, "player": player_str(value.name, value.position, value.team), "pos_rank": value.pos_rank,
           "ros_ppg": value.ros_ppg, "ppg": value.ppg, "expected_ppg": value.expected_ppg, "games": value.games,
           "trade_value": value.trade_value, "ecr": ecr.get(value.player_id),
           "schedule": matchup_words(value.schedule_factor) if round(value.schedule_factor, 2) != 1 else None,
           "playoff_schedule": schedule_words(value.playoff_factor),
           "rookie": True if value.is_rookie else None,
           "returning_starter": value.returning_starter,
           "injury": value.injury_note,
           "fill_in_played_well": label(value.fill_in) if value.fill_in else None}
    return {k: v for k, v in row.items() if v is not None}


def _rankings(session: Session, position: str | None = None, limit: int = 15, league: str | None = None,
              **fmt: Any) -> dict:
    from services.experts import weekly_ecr_by_player

    settings, row = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    values = [v for v in valuation.values if position is None or v.position == position][:limit]
    ecr = weekly_ecr_by_player(session)
    return {**header(valuation.league.scoring, default_week(valuation), row.name if row else None,
                     through_week=valuation.through_week, weeks_remaining=valuation.weeks_remaining),
            "results": [_ros_row(v, ecr, i) for i, v in enumerate(values, start=1)]}


def _leaderboard(session: Session, category: str, metric: str, season: int | None = None,
                 position: str | None = None, min_volume: int | None = None, limit: int = 15,
                 ascending: bool = False) -> dict:
    season = _checked_season(session, season) or latest_season(session)
    rows = _service(leaderboard, session, category, metric, season, min_volume, limit, ascending, position)
    return {"category": category, "metric": metric, "season": season, "results": [
        {"rank": r.rank, "player": player_str(r.name, r.position, r.team), "value": r.value, "volume": r.volume}
        for r in rows]}


def _metrics(_session: Session) -> dict:
    """Metric keys and labels only (the full catalog is too long for the model's budget)."""
    return {"categories": [{"category": c["key"], "metrics": [
        f"{m['key']}: {m['label']}{'' if m.get('higher_is_better', True) else ' (lower is better)'}"
        for m in c["metrics"]]} for c in catalog()]}


def _insights(kind: str):
    def handler(session: Session, position: str | None = None, limit: int = 8, league: str | None = None,
                **fmt: Any) -> dict:
        settings, _ = settings_for(session, league, **fmt)
        valuation = build_valuation(session, settings)
        return {"season": valuation.season, "through_week": valuation.through_week, "scoring": settings.scoring,
                "results": [{"player": player_str(i.name, i.position, i.team), "ppg": i.ppg,
                             "expected_ppg": i.expected_ppg, "fpoe_per_game": i.fpoe_per_game, "td_diff": i.td_diff,
                             "recent_ppg": i.recent_ppg, "trade_value": i.trade_value, "reasons": list(i.reasons)}
                            for i in find_insights(valuation, kind, position, limit)]}
    return handler


def _flags(rankings, valuation) -> dict[int, tuple]:
    if rankings.context is None:
        return {}
    seasons = {p.player_id: p for p in valuation.seasons}
    return {p.player_id: player_flags(seasons[p.player_id], rankings.context)
            for p in rankings.players if p.player_id in seasons}


def _weekly(session: Session, week: int | None = None, position: str | None = None,
            players: list[str] | None = None, limit: int = 12, league: str | None = None, **fmt: Any) -> dict:
    # Reads the stored snapshot only; agents never trigger outside fetches.
    settings, row = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    rankings = build_weekly_rankings(session, settings, week)
    wanted = {resolve_player(session, name).id: name for name in players} if players else None
    chosen = [p for p in rankings.players if (position is None or p.position == position)
              and (wanted is None or p.player_id in wanted)][:limit]
    flags, kickoff = _flags(rankings, valuation), kickoffs(chosen, valuation.schedule, rankings.week)
    result = {**header(rankings.scoring, rankings.week, row.name if row else None),
              "results": [weekly_row(p, flags=flags.get(p.player_id, ()), kickoff=kickoff.get(p.player_id),
                                     rank=i if position else p.rank) for i, p in enumerate(chosen, start=1)]}
    missing = [name for pid, name in (wanted or {}).items() if pid not in {p.player_id for p in chosen}]
    if missing:
        result["not_projected"] = missing
        result["note"] = "not_projected players are on bye, ruled out or not covered; try get_injury_report."
    return result


def _not_projected(session: Session, ref, rankings, valuation) -> str:
    """Why a player has no projection this week, as specific as the data allows."""
    availability = rankings.context.of(ref.id) if rankings.context else None
    team = getattr(ref, "team", None)
    if availability is not None and availability.play_prob <= 0:
        reason = f"is ruled out ({availability.label})"
    elif team and valuation.schedule.opponent(team, rankings.week) is None:
        reason = f"is on bye ({team})"
    else:
        reason = "is not covered by the projection sources"
    return f"{ref.name} {reason} in week {rankings.week}, so there is no projection. Try: get_injury_report."


def _explain(session: Session, player: str, week: int | None = None, league: str | None = None,
             **fmt: Any) -> dict:
    ref = resolve_player(session, player)
    settings, row = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    rankings = build_weekly_rankings(session, settings, week)
    item = next((p for p in rankings.players if p.player_id == ref.id), None)
    if item is None:
        raise ToolError(_not_projected(session, ref, rankings, valuation))
    kickoff = kickoffs([item], valuation.schedule, rankings.week).get(item.player_id)
    return {**header(rankings.scoring, rankings.week, row.name if row else None),
            **weekly_row(item, flags=_flags(rankings, valuation).get(item.player_id, ()), kickoff=kickoff),
            "steps": [f"{s.label}: {s.value}. {s.detail}" for s in item.explanation]}


def _waivers(session: Session, position: str | None = None, max_rostered: float = 50, limit: int = 10,
             league: str | None = None, **fmt: Any) -> dict:
    """Compact rows: the full waiver payload is too large for the model's token budget."""
    settings, _ = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    rankings = build_weekly_rankings(session, settings)
    return {**header(rankings.scoring, rankings.week), "results": [
        {"player": player_str(c.player.name, c.player.position, c.player.team), "pct_rostered": c.pct_rostered,
         "proj": c.player.projection, "ros_value": c.ros_value, "faab_pct": c.faab_pct, "why": c.reasons[1:3]}
        for c in waiver_candidates(rankings, valuation, position, max_rostered, limit)]}


def _find_trades(session: Session, my_players: list[str] | None = None, want_position: str | None = None,
                 league: str | None = None, **fmt: Any) -> dict:
    """Compact offers with each target's playoff (weeks 15-17) schedule factor.

    Without my_players, the user's roster in their imported Yahoo league is used.
    """
    settings, _ = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    ids, unknown = [], []
    if not my_players:
        ids = league_tools.my_roster_ids(session, league)
    for name in my_players or []:
        try:
            ids.append(resolve_player(session, name).id)
        except (PlayerNotFound, AmbiguousPlayer):
            unknown.append(name)                         # e.g. a rookie with no stats yet
    offers = find_trades(valuation, ids, want_position, 6)
    return {"perspective": TRADE_PERSPECTIVE, "skipped_players": unknown, "results": [
        {"give": [p.name for p in o.give], "get": player_str(o.get.name, o.get.position, o.get.team),
         "get_ros_ppg": o.get.ros_ppg, "verdict": o.evaluation.verdict,
         "your_gain_pct": round(o.evaluation.percent_difference * 100, 1),
         "get_playoff_schedule": schedule_words(o.get.playoff_factor)}
        for o in offers]}


def _schedule_strength(session: Session, position: str = "WR", week: int | None = None, team: str | None = None,
                       league: str | None = None, **fmt: Any) -> dict:
    settings, _ = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    data = serialize.schedule_strength(valuation, position, week or default_week(valuation))
    offenses = data["offenses"]
    if team:
        offenses = [o for o in offenses if o["team"] == team.strip().upper()]
        if not offenses:
            raise ToolError(f"No schedule for team '{label(team)}'. Try: an NFL abbreviation like team='DET'.")
    elif len(offenses) > 2 * SCHEDULE_TEAMS_SHOWN:
        offenses = offenses[:SCHEDULE_TEAMS_SHOWN] + offenses[-SCHEDULE_TEAMS_SHOWN:]
    return {"season": data["season"], "week": data["week"], "position": position,
            "note": ("defenses are listed easiest matchup first; 'easier' means the defense gives up more than "
                     "average to this position; allowed = fantasy points allowed per game; vs = this week's opponent"),
            "defenses": [{"team": d["team"], "matchup": matchup_words(d["sos_factor"]),
                          "allowed": round(d["allowed_per_game"], 1), "vs": d["this_week_opponent"]}
                         for d in data["defenses"]],
            "offenses": [{"team": o["team"], "this_week": o["this_week"],
                          "next_weeks": [f"{n['opponent']} ({matchup_words(n['sos_factor'])})" for n in o["next_weeks"]],
                          "rest_of_season": matchup_words(o["rest_of_season"]), "games_left": o["games_left"]}
                         for o in offenses]}


def _experts(session: Session, position: str | None = None, limit: int | None = None) -> dict:
    data = expert_rankings(session, position, limit or (25 if position else EXPERTS_PER_POSITION))
    return {"source": "expert consensus ranks (ECR, weekly snapshot)", "season": data["season"],
            "week": data["week"], "scope": data["scope"], "results": [
                {"player": player_str(r["name"], r["position"], r["team"]), "pos_rank": r["pos_rank"], "ecr": r["ecr"]}
                for r in data["results"]]}


def _trending(session: Session, type: str = "add", limit: int = 10) -> dict:
    try:
        return {"type": type, "source": "Sleeper", "results": trending_players(session, type, limit)}
    except SleeperError as exc:
        raise ToolUnavailable("Sleeper trending data is unavailable right now.") from exc


HANDLERS = {
    "search_players": _search, "get_player_profile": _profile, "get_player_game_log": _game_log,
    "compare_players": _compare, "get_fantasy_rankings": _rankings, "list_leaderboard_metrics": _metrics,
    "get_leaderboard": _leaderboard, "evaluate_trade": trade_tools.evaluate_trade, "get_buy_low_candidates": _insights("buy_low"),
    "get_sell_high_candidates": _insights("sell_high"), "get_weekly_rankings": _weekly,
    "get_schedule_strength": _schedule_strength, "explain_projection": _explain, "get_waiver_targets": _waivers,
    "find_trades": _find_trades, "get_my_team": league_tools.my_team, "get_league_waivers": league_tools.league_waivers,
    "find_league_trades": league_tools.league_trades, "get_league_rosters": league_tools.league_rosters,
    "get_my_matchup": league_tools.my_matchup, "get_playoff_outlook": league_tools.playoff_outlook,
    "get_power_rankings": league_tools.power_rankings, "get_player_news": league_tools.player_news,
    "remember": lambda session, fact: {"saved": fact.strip()},
    "get_expert_rankings": _experts, "get_trending_players": _trending,
}

TOOLS = ToolRegistry([Tool(name, description, parameters, HANDLERS[name], trim_keys)
                      for name, description, parameters, trim_keys in TOOL_SPECS] + [START_SIT, INJURY_REPORT])
