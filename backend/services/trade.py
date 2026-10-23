"""Trade evaluation built on rest-of-season trade values.

Packages are discounted: within each side, players are sorted by value and weighted
1.0, 0.9, 0.8, ... (never below 0.5). Lineup spots are limited, so two mid-tier players
are worth less than their raw sum. Both sides get the same treatment, so an even
2-for-2 stays even.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from services.valuation import PlayerValue

DEPTH_WEIGHT_STEP = 0.1
MIN_DEPTH_WEIGHT = 0.5
FAIR_THRESHOLD = 0.05        # within 5% -> fair
SLIGHT_THRESHOLD = 0.15      # within 15% -> slight edge
MAX_SUGGESTIONS = 5


@dataclass(frozen=True)
class SidePlayer:
    value: PlayerValue
    weight: float
    adjusted_value: float


@dataclass(frozen=True)
class TradeSide:
    players: tuple[SidePlayer, ...]
    raw_total: float
    adjusted_total: float


@dataclass(frozen=True)
class BalanceSuggestion:
    value: PlayerValue
    add_to: str                  # "give" or "get"
    resulting_percent_difference: float


@dataclass(frozen=True)
class TradeEvaluation:
    give: TradeSide
    get: TradeSide
    difference: float
    percent_difference: float
    verdict: str
    summary: str
    balance_suggestions: tuple[BalanceSuggestion, ...]


def depth_weight(index: int) -> float:
    return max(MIN_DEPTH_WEIGHT, round(1.0 - DEPTH_WEIGHT_STEP * index, 2))


def package(players: Sequence[PlayerValue]) -> TradeSide:
    ordered = sorted(players, key=lambda p: -p.trade_value)
    side = tuple(SidePlayer(value=p, weight=depth_weight(i),
                            adjusted_value=round(p.trade_value * depth_weight(i), 1))
                 for i, p in enumerate(ordered))
    return TradeSide(players=side, raw_total=round(sum(p.trade_value for p in ordered), 1),
                     adjusted_total=round(sum(p.adjusted_value for p in side), 1))


def percent_difference(give_total: float, get_total: float) -> float:
    largest = max(give_total, get_total)
    return round((get_total - give_total) / largest, 4) if largest > 0 else 0.0


def verdict_for(percent: float) -> str:
    size = abs(percent)
    if size <= FAIR_THRESHOLD:
        return "fair"
    direction = "win" if percent > 0 else "loss"
    return f"slight_{direction}" if size <= SLIGHT_THRESHOLD else f"big_{direction}"


def summarize(verdict: str, difference: float, percent: float, weeks_remaining: int) -> str:
    gap = f"{abs(difference):.1f} rest-of-season points ({abs(percent):.0%})"
    horizon = f"over the remaining {weeks_remaining} weeks"
    if verdict == "fair":
        return f"Fair trade: the sides are within {gap} {horizon}."
    edge = "slight edge" if verdict.startswith("slight") else "clear edge"
    if verdict.endswith("win"):
        return f"You win this trade by {gap} {horizon}, a {edge} in your favor."
    return f"You lose this trade by {gap} {horizon}, a {edge} for the other side."


def suggest_balance(give: Sequence[PlayerValue], get: Sequence[PlayerValue],
                    pool: Sequence[PlayerValue], percent: float) -> tuple[BalanceSuggestion, ...]:
    """Players whose addition to the short side brings the trade closest to even."""
    if verdict_for(percent) == "fair":
        return ()
    add_to = "get" if percent < 0 else "give"
    in_trade = {p.player_id for p in (*give, *get)}
    current = abs(percent)
    suggestions = []
    for candidate in pool:
        if candidate.player_id in in_trade or candidate.trade_value <= 0:
            continue
        new_give = [*give, candidate] if add_to == "give" else list(give)
        new_get = [*get, candidate] if add_to == "get" else list(get)
        new_percent = percent_difference(package(new_give).adjusted_total, package(new_get).adjusted_total)
        if abs(new_percent) < current:
            suggestions.append(BalanceSuggestion(candidate, add_to, new_percent))
    suggestions.sort(key=lambda s: (abs(s.resulting_percent_difference), -s.value.trade_value))
    return tuple(suggestions[:MAX_SUGGESTIONS])


def evaluate_trade(give: Sequence[PlayerValue], get: Sequence[PlayerValue],
                   pool: Sequence[PlayerValue], weeks_remaining: int) -> TradeEvaluation:
    if not give or not get:
        raise ValueError("Both sides of a trade need at least one player.")
    for side in (give, get):
        if len({p.player_id for p in side}) != len(side):
            raise ValueError("A player is listed more than once on the same side of the trade.")
    overlap = {p.player_id for p in give} & {p.player_id for p in get}
    if overlap:
        raise ValueError("A player can't be on both sides of the trade.")
    give_side, get_side = package(give), package(get)
    difference = round(get_side.adjusted_total - give_side.adjusted_total, 1)
    percent = percent_difference(give_side.adjusted_total, get_side.adjusted_total)
    verdict = verdict_for(percent)
    return TradeEvaluation(
        give=give_side, get=get_side, difference=difference, percent_difference=percent,
        verdict=verdict, summary=summarize(verdict, difference, percent, weeks_remaining),
        balance_suggestions=suggest_balance(give, get, pool, percent),
    )
