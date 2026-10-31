"""evaluate_trade for the agent: rest-of-season values for any trade, plus, when one of the user's
Yahoo leagues applies, what the trade does to both real starting lineups. Gains come from
services/league_trades.TradeScorer, the same scorer as the trade calculator and the league trade list.

In a league, balance suggestions only come from the two rosters involved: players you could add to
your side come from your roster, players to ask for come from the other team's roster.
"""
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from database.models import Team
from services import serialize
from services.labels import label
from services.players import resolve_player
from services.trade import evaluate_trade as evaluate
from services.trade import suggest_balance
from services.valuation import build_valuation
from tools.league_tools import TRADE_PERSPECTIVE, settings_for
from tools.registry import ToolError
from tools.rows import player_str

LINEUP_NOTE = ("your_lineup_gain / their_lineup_gain: points each team's best starting lineup gains over the rest "
               "of the season (this app's projections). acceptable_to_them = their lineup doesn't lose much; it is "
               "not a promise the other manager accepts.")


def _checked(call, *args):
    """Our own validation ValueErrors, as ToolErrors (library messages never reach here)."""
    try:
        return call(*args)
    except ValueError as exc:
        raise ToolError(str(exc)[:300]) from exc


def _values(session: Session, valuation, names: list[str]) -> list:
    resolved = []
    for name in names:
        ref = resolve_player(session, name)
        value = valuation.by_id.get(ref.id)
        if value is None:
            raise ToolError(f"{ref.name} has no {valuation.season} regular-season games, so there is no "
                            "rest-of-season value. Try: leave him out of the trade.")
        resolved.append(value)
    return resolved


def _partner(session: Session, scorer, my_ids: set[int], get: list) -> tuple[int | None, str | None, set[int]]:
    """(team id, name, roster ids) of the team rostering every `get` player; ToolError for impossible trades."""
    owner = {s.player_id: team_id for team_id, spots in scorer.rosters.items() for s in spots}
    get_ids = [p.player_id for p in get]
    if any(pid in my_ids for pid in get_ids):
        raise ToolError("A player you'd receive is already on your roster. Try: put him in `give` instead.")
    partners = {owner[pid] for pid in get_ids if pid in owner}
    if len(partners) > 1:
        raise ToolError("The players you'd receive are on different teams. Try: one trade per team.")
    partner_id = next(iter(partners), None)
    if partner_id is None or any(pid not in owner for pid in get_ids):
        return None, None, set()
    team = session.get(Team, partner_id)
    return partner_id, team.name if team else None, {s.player_id for s in scorer.rosters.get(partner_id, [])}


def _lineup_fields(scorer, give: list, get: list, partner_id: int | None, my_ids: set[int]) -> dict:
    if partner_id is None:
        return {"lineup_note": "the players you'd receive aren't on a team in this league, so lineup gains "
                               "aren't computed"}
    if any(p.player_id not in my_ids for p in give):
        return {"lineup_note": "a player you'd send isn't on your roster, so lineup gains aren't computed"}
    result = scorer.score(give, get, partner_id)
    named = {key: player_str(v.name, v.position, v.team) for key, v in
             (("their_drop", result["their_drop"]), ("your_add", result["my_add"]), ("your_drop", result["my_drop"]))
             if v is not None}
    return {"your_lineup_gain": result["your_gain"], "their_lineup_gain": result["their_gain"],
            "acceptable_to_them": result["their_gain"] >= scorer.floor, **named, "lineup_note": LINEUP_NOTE}


def _league_part(session: Session, row, evaluation, give: list, get: list, valuation) -> tuple[dict, Any]:
    """({lineup fields}, evaluation with balance suggestions limited to the two rosters)."""
    from services.league_trades import TradeScorer

    scorer = TradeScorer(session, row.id)
    my_ids = {s.player_id for s in scorer.team.spots}
    partner_id, partner, partner_ids = _partner(session, scorer, my_ids, get)
    # suggest_balance adds to `get` when the user is behind (ask the partner for more), else to `give`.
    side = partner_ids if evaluation.percent_difference < 0 else my_ids
    pool = [valuation.by_id[pid] for pid in side if pid in valuation.by_id]
    suggestions = suggest_balance(give, get, pool, evaluation.percent_difference)
    fields = {"league": label(row.name), "partner": label(partner) if partner else None,
              **_lineup_fields(scorer, give, get, partner_id, my_ids)}
    return {k: v for k, v in fields.items() if v is not None}, replace(evaluation, balance_suggestions=suggestions)


def evaluate_trade(session: Session, give: list[str], get: list[str], league: str | None = None,
                   **fmt: Any) -> dict:
    settings, row = settings_for(session, league, **fmt)
    valuation = build_valuation(session, settings)
    give_values, get_values = _values(session, valuation, give), _values(session, valuation, get)
    evaluation = _checked(evaluate, give_values, get_values, valuation.values, valuation.weeks_remaining)
    lineup: dict = {}
    if row is not None:
        lineup, evaluation = _league_part(session, row, evaluation, give_values, get_values, valuation)
    return {"perspective": TRADE_PERSPECTIVE, "scoring": settings.scoring, **lineup,
            **serialize.trade(evaluation, valuation.weeks_remaining)}
