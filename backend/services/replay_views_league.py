"""Replay League: the league-wide screens (standings, schedule, activity, the waiver wire, a player's page,
claims and trades). The JSON shapes are documented at the top of services/replay_views.py; the same
no-hindsight rule holds here (`Game.data` and `Game.know_at` only).
"""
import json
from collections import defaultdict
from collections.abc import Sequence
from statistics import mean

from database.models import ReplayLeague
from services import replay_season, replay_state as rs, replay_trades, replay_waivers
from services.replay_pool import Usage
from services.replay_state import Offer, Team
from services.replay_trades import Verdict
from services.replay_values import ReplayValue
from services.replay_views import (
    DEFAULT_PAGE, Game, event_row, page, player_ref, player_row, positions_for, record, refs,
    result_of, scoreboard, team_ref, winner,
)

EVENT_KINDS = ("draft", "add", "drop", "claim", "trade", "ir", "result", "playoffs", "champion")
AVAILABLE = "available"
STATUSES = (AVAILABLE, *replay_waivers.STATUS_FILTERS)
SORTS = ("value", "projection", "ros_ppg", "adp", "points", "name")
USAGE_GAMES = 3                         # games averaged in a WireRow's usage


def listing(row: ReplayLeague) -> dict:
    """A saved game for the list of games, read from the stored JSON without rebuilding the state."""
    state, settings = json.loads(row.state), json.loads(row.settings)
    champion = next((t["name"] for t in state["teams"] if t["id"] == state.get("champion")), None)
    return {"id": row.id, "name": row.name, "season": row.season, "phase": state["phase"], "week": state["week"],
            "teams": settings["teams"], "scoring": settings["scoring"], "champion": champion,
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


# ---------------- standings, schedule, activity ----------------

def standings(game: Game) -> dict:
    """The table (final order once the season is over, else by seed) and the playoff games known so far."""
    state, settings = game.state, game.settings
    teams = {t.id: t for t in state.teams}
    playoff_weeks = range(settings.regular_season_weeks + 1, settings.end_week + 1)
    rows = [{**team_ref(game, teams[row["team_id"]]), **record(game, row["team_id"]), "seed": row["rank"],
             "in_playoffs": row["rank"] <= replay_season.BRACKET_SIZE} for row in game.table]
    return {"results": rows, "regular_season_weeks": settings.regular_season_weeks,
            "playoff_teams": replay_season.BRACKET_SIZE,
            "seeds_final": result_of(state, settings.regular_season_weeks) is not None,
            "bracket": [scoreboard(game, week) for week in playoff_weeks
                        if state.phase != "draft" and replay_season.matchups_for_week(state, settings, week)],
            "champion": team_ref(game, teams[state.champion]) if state.champion in teams else None}


def _schedule_row(game: Game, owner: Team, week: int) -> dict:
    state, result = game.state, result_of(game.state, week)
    pair = replay_season.matchup_for(state, game.settings, owner.id, week)
    opponent = next((rs.team(state, team_id) for team_id in pair or () if team_id != owner.id), None)
    won = winner(game, pair, week) if pair and result else None
    outcome = None if result is None else "T" if won is None else "W" if won == owner.id else "L"
    return {"week": week, "playoff": replay_season.is_playoff_week(game.settings, week),
            "played": result is not None, "opponent": team_ref(game, opponent) if opponent else None,
            "points": result.scores[owner.id] if result else None,
            "opponent_points": result.scores[opponent.id] if result and opponent else None, "result": outcome}


def schedule(game: Game, team_id: int | None = None) -> dict:
    """A team's season (default: the user's): every week's opponent, and the score once played. A playoff
    opponent is null until it is known; empty during the draft."""
    owner = rs.team(game.state, game.user.id if team_id is None else team_id)
    weeks = range(1, game.settings.end_week + 1) if game.state.phase != "draft" else ()
    return {"team": team_ref(game, owner), "results": [_schedule_row(game, owner, week) for week in weeks]}


def activity(game: Game, kind: str | None = None, limit: int = DEFAULT_PAGE, offset: int = 0) -> dict:
    """The league's log, newest first. `kind` is one event kind or several joined by commas."""
    kinds = {part.strip() for part in kind.split(",")} if kind else set(EVENT_KINDS)
    unknown = sorted(kinds - set(EVENT_KINDS))
    if unknown:
        raise ValueError(f"Unknown activity kind '{unknown[0]}'. Valid: {list(EVENT_KINDS)}")
    events = [event for event in reversed(game.state.events) if event.kind in kinds]
    return page(events, limit, offset, lambda event: event_row(game, event))


# ---------------- players ----------------

def _owners(game: Game) -> dict[int, Team]:
    return {pid: owner for owner in game.state.teams for pid in rs.held(owner)}


def _season_points(game: Game) -> dict[int, float]:
    """{player id: real points so far}: `Game.data` holds played weeks only."""
    totals: dict[int, float] = defaultdict(float)
    for (player_id, _), points in game.data.actual.items():
        totals[player_id] += points
    return totals


def _usage(games: Sequence[Usage]) -> dict | None:
    """The Usage shape: means over `games`; snap % and target share over the games that have them."""
    if not games:
        return None
    snaps = [g.snap_pct for g in games if g.snap_pct is not None]
    shares = [g.target_share for g in games if g.target_share is not None]
    return {"games": len(games), "snap_pct": round(mean(snaps), 1) if snaps else None,
            "targets": round(mean(g.targets for g in games), 1),
            "target_share": round(mean(shares), 3) if shares else None,
            "carries": round(mean(g.carries for g in games), 1)}


def _recent_usage(game: Game) -> dict[int, dict]:
    """{player id: Usage of his last USAGE_GAMES games}: `Game.data` holds played weeks only (skill games)."""
    games: dict[int, list[Usage]] = defaultdict(list)
    for (player_id, _), usage in sorted(game.data.usage.items(), key=lambda item: -item[0][1]):
        if len(games[player_id]) < USAGE_GAMES:
            games[player_id].append(usage)
    return {player_id: _usage(recent) for player_id, recent in games.items()}


def _wire_row(game: Game, player_id: int, owners: dict[int, Team], totals: dict[int, float],
              usage: dict[int, dict]) -> dict:
    owner = owners.get(player_id)
    return {**player_row(game, game.know, player_id),
            "status": replay_waivers.player_status(game.state, player_id),
            "owner": team_ref(game, owner) if owner else None, "season_points": round(totals.get(player_id, 0.0), 2),
            "last_points": game.data.actual.get((player_id, game.played)), "usage": usage.get(player_id)}


def _sorted(values: list[ReplayValue], sort: str, totals: dict[int, float]) -> list[ReplayValue]:
    """`values` arrive best value first, so every sort breaks its ties that way."""
    keys = {
        "projection": lambda v: -v.projection, "ros_ppg": lambda v: -v.ros_ppg,
        "adp": lambda v: (v.adp is None, v.adp or 0.0), "points": lambda v: -totals.get(v.player_id, 0.0),
        "name": lambda v: v.name.lower(),
    }
    if sort not in SORTS:
        raise ValueError(f"Unknown sort '{sort}'. Valid: {list(SORTS)}")
    return values if sort == "value" else sorted(values, key=keys[sort])


def players(game: Game, position: str | None = None, search: str | None = None, status: str | None = None,
            sort: str = "value", limit: int = DEFAULT_PAGE, offset: int = 0) -> dict:
    """The waiver wire: the pool filtered by position (FLEX = RB/WR/TE), name and status, one page of it.
    The default status "available" is everyone not on a roster."""
    status = status or AVAILABLE
    if status not in STATUSES:
        raise ValueError(f"Unknown status '{status}'. Valid: {list(STATUSES)}")
    wanted, totals, owners, usage = positions_for(position), _season_points(game), _owners(game), _recent_usage(game)
    found = replay_waivers.available_players(game.state, game.know, None, search,
                                             None if status == AVAILABLE else status)
    values = _sorted([value for value in found if value.position in wanted], sort, totals)
    return {**page(values, limit, offset, lambda v: _wire_row(game, v.player_id, owners, totals, usage)),
            "week": game.state.week}


def _history_row(game: Game, player_id: int, week: int) -> dict:
    row, usage = player_row(game, game.know_at(week), player_id), game.data.usage.get((player_id, week))
    return {"week": week, "played": week <= game.played, "team": row["player"]["team"],
            "injury": row["player"]["injury"],
            **{key: row[key] for key in ("opponent", "bye", "out", "projection", "points", "ros_ppg", "value",
                                         "rank", "pos_rank", "expert_rank", "news")},
            "usage": _usage([usage] if usage else [])}          # played weeks only: `Game.data`


def player(game: Game, player_id: int) -> dict:
    """One player: where he stands now and his weeks so far (projection against result, value by week),
    from the week he entered the pool (a late arrival has no earlier weeks)."""
    if player_id not in game.know.by_id:
        raise rs.ReplayNotFound(f"There is no player {player_id} in the {game.settings.season} player pool.")
    return {"player": _wire_row(game, player_id, _owners(game), _season_points(game), _recent_usage(game)),
            "weeks": [_history_row(game, player_id, week) for week in range(1, game.state.week + 1)
                      if player_id in game.know_at(week).by_id]}


# ---------------- claims and trades ----------------

def claims(game: Game) -> dict:
    """The user's pending waiver claims in preference order, the league's waiver priority and, in a FAAB
    league, every team's budget left (public, as in Yahoo; other teams' pending bids never are)."""
    state, know = game.state, game.know
    order, mine = list(state.waiver_order), [claim for claim in state.claims if claim.team_id == game.user.id]
    faab = None
    if game.settings.waiver_mode == "faab":
        faab = {"budget": rs.FAAB_BUDGET, "left": rs.faab_left(state, game.user.id),
                "teams": [{"team": team_ref(game, rs.team(state, team_id)), "left": rs.faab_left(state, team_id)}
                          for team_id in order]}
    return {"phase": state.phase, "can_claim": state.phase == "waivers",
            "priority": order.index(game.user.id) + 1 if game.user.id in order else None,
            "waiver_order": [team_ref(game, rs.team(state, team_id)) for team_id in order], "faab": faab,
            "results": [{"id": claim.id, "order": place, "add": player_row(game, know, claim.add),
                         "drop": player_row(game, know, claim.drop) if claim.drop is not None else None,
                         "bid": claim.bid}
                        for place, claim in enumerate(mine, start=1)]}


def verdict(game: Game, found: Verdict) -> dict:
    """A trade verdict from the user's side: "give" is what the user sends."""
    by_id = game.know.by_id

    def priced(pairs: tuple[tuple[int, float], ...]) -> list[dict]:
        return [{"player": player_ref(by_id[pid]), "value": value} for pid, value in pairs]

    return {"accepted": found.accepted, "reasons": list(found.reasons), "fair_label": found.fair_label,
            "value_give": round(found.value_give, 1), "value_get": round(found.value_get, 1),
            "percent": found.percent, "your_gain": found.your_gain, "their_gain": found.their_gain,
            "give": priced(found.give_values), "get": priced(found.get_values),
            "your_drops": refs(game, found.your_drops), "their_drops": refs(game, found.their_drops),
            "suggestion": found.suggestion}


def offer_row(game: Game, offer: Offer) -> dict:
    state = game.state
    return {"id": offer.id, "week": offer.week, "status": offer.status,
            "from_team": team_ref(game, rs.team(state, offer.from_team)),
            "to_team": team_ref(game, rs.team(state, offer.to_team)),
            "give": refs(game, offer.give), "get": refs(game, offer.get), "counter_to": offer.counter_to}


def _preview(game: Game, offer: Offer) -> dict:
    """A CPU's pending offer judged from the user's side (the user would send `offer.get`)."""
    try:
        found = replay_trades.evaluate(game.state, game.settings, game.know, game.user.id, offer.from_team,
                                       offer.get, offer.give)
    except ValueError as exc:               # e.g. the user's roster would overflow: accepting needs a drop
        return {"verdict": None, "note": str(exc)}
    return {"verdict": verdict(game, found), "note": None}


def trades(game: Game) -> dict:
    """The trade screen: CPU offers waiting for an answer (with a preview verdict) and the league's history."""
    state, settings = game.state, game.settings
    pending = replay_trades.offers_for_user(state)
    waiting = {offer.id for offer in pending}
    return {"open": state.phase in replay_trades.TRADE_PHASES and rs.trades_open(state, settings),
            "deadline_week": settings.trade_deadline_week, "max_players": replay_trades.MAX_PLAYERS_PER_SIDE,
            "offers": [{**offer_row(game, offer), **_preview(game, offer)} for offer in pending],
            "results": [offer_row(game, offer) for offer in reversed(state.offers) if offer.id not in waiting]}
