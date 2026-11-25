"""Replay League: the league-wide screens (standings, schedule, activity, claims and trades; the waiver wire
and a player's page are in services/replay_views_players.py and re-exported here). The JSON shapes are
documented at the top of services/replay_views.py; the same no-hindsight rule holds here (`Game.data` and
`Game.know_at` only).
"""
import json

from database.models import ReplayLeague
from services import replay_chat, replay_season, replay_state as rs, replay_trade_value, replay_trades
from services.replay_state import Chat, Offer, Team
from services.replay_trades import Verdict
from services.replay_views import (
    DEFAULT_PAGE, Game, event_row, league_kind, page, player_ref, player_row, record, refs, result_of, team_ref,
    winner,
)
from services.replay_views_players import (  # noqa: F401 - re-exported: the wire and a player's page live there
    AVAILABLE, SORTS, STATUSES, USAGE_GAMES, player, players,
)
from services.replay_views_box import box_score  # noqa: F401 - re-exported: a played game's box score
from services.replay_views_playoffs import playoffs  # noqa: F401 - re-exported: the bracket view lives there

EVENT_KINDS = ("draft", "add", "drop", "claim", "trade", "ir", "result", "playoffs", "champion")


def listing(row: ReplayLeague) -> dict:
    """A saved game for the list of games, read from the stored JSON without rebuilding the state. kind: as in
    Summary (replay | live | future)."""
    state, settings = json.loads(row.state), json.loads(row.settings)
    champion = next((t["name"] for t in state["teams"] if t["id"] == state.get("champion")), None)
    return {"id": row.id, "name": row.name, "season": row.season, "phase": state["phase"], "week": state["week"],
            "teams": settings["teams"], "scoring": settings["scoring"], "champion": champion,
            "kind": league_kind(settings.get("sim_from")),
            "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()}


# ---------------- standings, schedule, activity ----------------

def standings(game: Game) -> dict:
    """The table (final order once the season is over, else by seed); the bracket is replay_views_playoffs'."""
    state, settings = game.state, game.settings
    teams = {t.id: t for t in state.teams}
    playoff_weeks = range(settings.regular_season_weeks + 1, settings.end_week + 1)
    rows = [{**team_ref(game, teams[row["team_id"]]), **record(game, row["team_id"]), "seed": row["rank"],
             "in_playoffs": row["rank"] <= settings.playoff_teams} for row in game.table]
    return {"results": rows, "regular_season_weeks": settings.regular_season_weeks,
            "playoff_teams": settings.playoff_teams, "playoff_weeks": list(playoff_weeks),
            "byes": 2 ** rs.playoff_rounds(settings.playoff_teams) - settings.playoff_teams,
            "seeds_final": result_of(state, settings.regular_season_weeks) is not None}


def _schedule_row(game: Game, owner: Team, week: int) -> dict:
    state, result = game.state, result_of(game.state, week)
    known = replay_season.matchups_for_week(state, game.settings, week)
    pair = next((teams for teams in known if owner.id in teams), None)
    opponent = next((rs.team(state, team_id) for team_id in pair or () if team_id != owner.id), None)
    won = winner(game, pair, week) if pair and result else None
    outcome = None if result is None or pair is None else "T" if won is None else "W" if won == owner.id else "L"
    return {"week": week, "playoff": replay_season.is_playoff_week(game.settings, week),
            "played": result is not None, "idle": bool(known) and pair is None,
            "opponent": team_ref(game, opponent) if opponent else None,
            "points": result.scores.get(owner.id) if result else None,
            "opponent_points": result.scores[opponent.id] if result and opponent else None, "result": outcome}


def schedule(game: Game, team_id: int | None = None) -> dict:
    """A team's season (default: the user's): every week's opponent, and the score once played. A playoff
    opponent is null until it is known; empty during the draft. A jump-in league's season starts at start_week."""
    owner = rs.team(game.state, game.user.id if team_id is None else team_id)
    first = game.settings.start_week
    weeks = range(first, game.settings.end_week + 1) if game.state.phase != "draft" else ()
    return {"team": team_ref(game, owner), "results": [_schedule_row(game, owner, week) for week in weeks]}


def activity(game: Game, kind: str | None = None, limit: int = DEFAULT_PAGE, offset: int = 0) -> dict:
    """The league's log, newest first. `kind` is one event kind or several joined by commas."""
    kinds = {part.strip() for part in kind.split(",")} if kind else set(EVENT_KINDS)
    unknown = sorted(kinds - set(EVENT_KINDS))
    if unknown:
        raise ValueError(f"Unknown activity kind '{unknown[0]}'. Valid: {list(EVENT_KINDS)}")
    events = [event for event in reversed(game.state.events) if event.kind in kinds]
    return page(events, limit, offset, lambda event: event_row(game, event))


def chat_row(game: Game, line: Chat) -> dict:
    return {"id": line.id, "week": line.week, "team": team_ref(game, rs.team(game.state, line.team_id)),
            "to_user": line.to_team == game.user.id, "kind": line.kind, "text": line.text,
            "players": refs(game, line.player_ids)}


def chat(game: Game, limit: int = DEFAULT_PAGE, offset: int = 0) -> dict:
    """The league chat, newest first (offer messages show on their offers instead)."""
    return page(list(reversed(replay_chat.chat_for(game.state))), limit, offset, lambda line: chat_row(game, line))


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


PICKUP_NOTE = (f"Just picked up: counts {replay_trade_value.PICKUP_SHARE:.0%} of his value in trades this week")


def verdict(game: Game, found: Verdict) -> dict:
    """A trade verdict from the user's side: "give" is what the user sends. A row priced below his chart value
    is a player picked up this week (replay_trade_value.fresh) and says so in its `note`."""
    by_id = game.know.by_id

    def priced(pairs: tuple[tuple[int, float], ...]) -> list[dict]:
        return [{"player": player_ref(by_id[pid]), "value": value,
                 "note": PICKUP_NOTE if value < by_id[pid].chart else None} for pid, value in pairs]

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
            "give": refs(game, offer.give), "get": refs(game, offer.get), "counter_to": offer.counter_to,
            "message": offer.message or None}


def _preview(game: Game, offer: Offer) -> dict:
    """A CPU's pending offer judged from the user's side (the user would send `offer.get`)."""
    try:
        found = replay_trades.evaluate(game.state, game.settings, game.know, game.user.id, offer.from_team,
                                       offer.get, offer.give, offered=True)
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
            "results": [offer_row(game, offer) for offer in reversed(state.offers) if offer.id not in waiting],
            "shops": _shops(game)}


def _shops(game: Game) -> list[dict]:
    """Players CPUs are shopping to the user now, with the message each sent (never the discount)."""
    state = game.state
    if not (state.phase in replay_trades.TRADE_PHASES and rs.trades_open(state, game.settings)):
        return []
    rows = []
    for team in (t for t in state.teams if not t.is_user):
        for pid in rs.markdowns(state, team.id):
            said = next((c.text for c in reversed(state.chat)
                         if c.kind == "shop" and c.team_id == team.id and pid in c.player_ids), None)
            rows.append({"team": team_ref(game, team), "player": player_ref(game.know.by_id[pid]), "message": said})
    return rows
