"""Replay League CPU personas: each CPU manager trades with a style, assigned once from the game's seed.

`private_value` is what a persona thinks a player is worth (the chart, nudged, clamped to +-PRIVATE_CLAMP);
`offer_rate` / `counter_rate` how often it offers and counters; `tendency` a hint shown to the user.
Pure. See .plans/pr1-plan.md section D.
"""
from collections.abc import Sequence

from services.replay_values import ReplayValue

PERSONAS = ("balanced", "panicky", "shark", "steady", "gambler")
PRIVATE_CLAMP = 0.15           # private value stays within +-15% of the chart
TENDENCY_WEEKS = 3             # no tendency shown before this many weeks played


def assign(seed: int, cpu_ids: Sequence[int]) -> dict[int, str]:
    """{cpu team id: persona}; at least one shark and one panicky when there are two or more CPUs."""
    raise NotImplementedError


def private_value(value: ReplayValue, persona: str) -> float:
    raise NotImplementedError


def offer_rate(persona: str) -> float:
    raise NotImplementedError


def counter_rate(persona: str) -> float:
    raise NotImplementedError


def tendency(persona: str, weeks_played: int) -> str | None:
    """A one-line hint about how this manager trades; None before TENDENCY_WEEKS weeks."""
    raise NotImplementedError
