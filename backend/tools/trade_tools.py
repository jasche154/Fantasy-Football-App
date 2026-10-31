"""evaluate_trade for the agent: rest-of-season values for any trade, plus, when one of the user's
Yahoo leagues applies, what the trade does to both real starting lineups (services/league_moves.score_trade).

In a league, balance suggestions only come from the two rosters involved: players you could add to
your side come from your roster, players to ask for come from the other team's roster.
"""
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from services import serialize
from services.labels import label
from services.players import resolve_player
from services.trade import evaluate_trade as evaluate
from services.trade import suggest_balance
from services.valuation import build_valuation
from tools.league_tools import TRADE_PERSPECTIVE, settings_for
from tools.registry import ToolError

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


def _league_part(session: Session, row, evaluation, give: list, get: list, valuation) -> tuple[dict, Any]:
    """({lineup fields}, evaluation with balance suggestions limited to the two rosters)."""
    from services.league_moves import score_trade

    score = _checked(score_trade, session, row.id, [p.player_id for p in give], [p.player_id for p in get])
    # suggest_balance adds to `get` when the user is behind (ask the partner for more), else to `give`.
    side = score.partner_ids if evaluation.percent_difference < 0 else score.my_ids
    pool = [valuation.by_id[pid] for pid in side if pid in valuation.by_id]
    suggestions = suggest_balance(give, get, pool, evaluation.percent_difference)
    fields = {"league": label(row.name), "partner": label(score.partner) if score.partner else None,
              "your_lineup_gain": score.your_lineup_gain, "their_lineup_gain": score.their_lineup_gain,
              "acceptable_to_them": score.acceptable_to_them,
              "lineup_note": score.note or LINEUP_NOTE}
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
