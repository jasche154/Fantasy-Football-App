"""Replay League trade scoring: one CPU's net on the 1000 chart scale for a deal (`tally`).

score = in + premium + spot + 0.5 x lineup - out x multiple x 1.03; see .plans/pr1-plan.md section D.
Pure: takes a Knowledge and the players, no state.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from services.replay_values import Knowledge

STAR_PREMIUM = 0.12            # x B x (1 - v2 / B): the best player in the deal is worth more than parts
GAMBLER_PREMIUM = 0.18
SPOT_VALUE = 35.0              # chart points per roster spot freed
LINEUP_PER_WEEK = 60.0         # chart points per lineup point a week
LINEUP_CAP = 0.15              # lineup term clamped to +- this share of `out`
LINEUP_WEIGHT = 0.5
OUT_MARGIN = 1.03
OUT_SELL_SHARE = 0.5           # an out player sent to the user counts >= this x healthy_chart
MARKET_GUARD = 0.80            # neutral market in >= this x out, whatever the persona
SHARK_MARGIN = 0.05


@dataclass(frozen=True)
class Tally:
    sent: float
    received: float
    premium: float
    spot: float
    lineup: float
    score: float


def tally(know: Knowledge, persona: str, sends: Sequence[int], kept: Sequence[int], drops: Sequence[int],
          gain_per_week: float, multiple: float, to_user: bool) -> Tally:
    """The CPU sends `sends`, keeps `kept` of what it receives and cuts `drops`."""
    raise NotImplementedError
