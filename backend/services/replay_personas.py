"""Replay League CPU personas: each CPU manager trades with a style, assigned once from the game's seed.

`private_value` is what a persona thinks a player is worth (the chart, nudged, clamped to +-PRIVATE_CLAMP);
`offer_rate` / `counter_rate` how often it offers and counters; `tendency` a hint shown to the user.

- balanced: the chart.
- panicky: marks a player down after bad games (0.8 x a negative form_chart): sells low.
- steady: trusts draft day (0.35 x star x (pre_chart - chart)): won't sell a slumping star cheap.
- gambler: chases hot streaks (0.5 x a positive form_chart) and pays more for the best player in a deal.
- shark: the chart, but wants a SHARK_MARGIN edge; hunts buy-low stars and offers the user the most.
Pure.
"""
from collections.abc import Sequence

from services import replay_state as rs
from services.replay_values import ReplayValue

PERSONAS = ("balanced", "panicky", "shark", "steady", "gambler")
PRIVATE_CLAMP = 0.15           # private value stays within +-15% of the chart
TENDENCY_WEEKS = 3             # no tendency shown before this many weeks played
PANIC_FORM = 0.8
STEADY_STAR = 0.35
GAMBLER_FORM = 0.5
OFFER_RATE = {"balanced": 0.08, "panicky": 0.12, "shark": 0.30, "steady": 0.05, "gambler": 0.12}
COUNTER_RATE = {"balanced": 0.5, "panicky": 0.4, "shark": 0.8, "steady": 0.3, "gambler": 0.6}
TENDENCY = {
    "balanced": "Trades on fair value",
    "panicky": "Sells after a bad week",
    "shark": "Hunts for buy-low stars",
    "steady": "Slow to give up on stars",
    "gambler": "Chases hot streaks",
}


def assign(seed: int, cpu_ids: Sequence[int]) -> dict[int, str]:
    """{cpu team id: persona}; at least one shark and one panicky when there are two or more CPUs."""
    dice = rs.rng(seed, "personas")
    order = [cpu_ids[int(i)] for i in dice.permutation(len(cpu_ids))]
    picked = {team_id: PERSONAS[int(dice.integers(len(PERSONAS)))] for team_id in order}
    if len(order) >= 2:
        picked |= {order[0]: "shark", order[1]: "panicky"}
    return {team_id: picked[team_id] for team_id in cpu_ids}


def private_value(value: ReplayValue, persona: str) -> float:
    chart = value.chart
    if persona == "panicky":
        nudged = chart + PANIC_FORM * min(0.0, value.form_chart)
    elif persona == "steady":
        nudged = chart + STEADY_STAR * value.star * (value.pre_chart - chart)
    elif persona == "gambler":
        nudged = chart + GAMBLER_FORM * max(0.0, value.form_chart)
    else:
        nudged = chart
    return min(max(nudged, chart * (1 - PRIVATE_CLAMP)), chart * (1 + PRIVATE_CLAMP))


def offer_rate(persona: str) -> float:
    return OFFER_RATE.get(persona, OFFER_RATE["balanced"])


def counter_rate(persona: str) -> float:
    return COUNTER_RATE.get(persona, COUNTER_RATE["balanced"])


def tendency(persona: str, weeks_played: int) -> str | None:
    """A one-line hint about how this manager trades; None before TENDENCY_WEEKS weeks."""
    return TENDENCY.get(persona) if weeks_played >= TENDENCY_WEEKS else None
