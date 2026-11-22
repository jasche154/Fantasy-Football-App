"""Replay League trade scoring: one CPU's net on the 1000 chart scale for a deal (`tally`).

score = in + premium + spot + 0.5 x lineup - out x multiple x 1.03 (a shark also wants SHARK_MARGIN x out):
- out: what it sends, at its persona's private value; an out player it sends the user counts at least
  OUT_SELL_SHARE of his healthy chart (the user may know he is back soon).
- in: what it receives and keeps, minus its own players it must cut to make room.
- premium: the best player in the deal is worth more than parts: rate x (B - v2), B the best chart in the
  deal and v2 the other side's best. Signed: + when the CPU gets B, - when it sends him.
- spot: SPOT_VALUE per roster spot the deal frees.
- lineup: LINEUP_PER_WEEK x its lineup gain a week, clamped to +- LINEUP_CAP x out.
The multiple is 1.00 (normal) or 1.08 (hard) against the user and 0.95 between CPUs; a CPU accepts at
score >= 0 when `market_ok` (the anti-stupid guard on neutral chart values, whatever the persona) holds too.
Pure: takes a Knowledge and the players, no state.
"""
from collections.abc import Sequence
from dataclasses import dataclass

from services import replay_personas
from services.replay_values import Knowledge

STAR_PREMIUM = 0.12            # x B x (1 - v2 / B): the best player in the deal is worth more than parts
GAMBLER_PREMIUM = 0.18
SPOT_VALUE = 35.0              # chart points per roster spot freed
LINEUP_PER_WEEK = 60.0         # chart points per lineup point a week
LINEUP_CAP = 0.15              # lineup term clamped to +- this share of `out`
LINEUP_WEIGHT = 0.5            # a lineup gain counts half
LINEUP_LOSS_WEIGHT = 1.0       # a lineup loss counts in full: a CPU feels losing a starter (Kelce for Goedert + Davis)
OUT_MARGIN = 1.03
OUT_SELL_SHARE = 0.5           # an out player sent to the user counts >= this x healthy_chart
MARKET_GUARD = 0.80            # neutral market in >= this x out, whatever the persona
SHARK_MARGIN = 0.05


@dataclass(frozen=True)
class Tally:
    sent: float                # its private value of what it sends (sell floor included)
    received: float            # of what it keeps, minus its forced drops
    premium: float             # signed: + it gets the best player in the deal, - it sends him
    spot: float
    lineup: float              # before LINEUP_WEIGHT / LINEUP_LOSS_WEIGHT
    score: float               # accept at >= 0


def _best(know: Knowledge, player_ids: Sequence[int]) -> float:
    return max((know.by_id[pid].chart for pid in player_ids), default=0.0)


def premium(know: Knowledge, persona: str, sends: Sequence[int], kept: Sequence[int]) -> float:
    """rate x (B - v2), + when the best player in the deal is among `kept`, - when among `sends`."""
    rate = GAMBLER_PREMIUM if persona == "gambler" else STAR_PREMIUM
    return rate * (_best(know, kept) - _best(know, sends))


def tally(know: Knowledge, persona: str, sends: Sequence[int], kept: Sequence[int], drops: Sequence[int],
          gain_per_week: float, multiple: float, to_user: bool) -> Tally:
    """The CPU sends `sends`, keeps `kept` of what it receives and cuts `drops` (its own players)."""
    def private(pid: int) -> float:
        return replay_personas.private_value(know.by_id[pid], persona)

    def sell(pid: int) -> float:
        value = know.by_id[pid]
        floor = OUT_SELL_SHARE * value.healthy_chart if to_user and value.out else 0.0
        return max(private(pid), floor)

    sent = sum(sell(pid) for pid in sends)
    received = sum(private(pid) for pid in kept) - sum(private(pid) for pid in drops)
    bonus = premium(know, persona, sends, kept)
    spot = SPOT_VALUE * max(0, len(sends) - len(kept))
    cap = LINEUP_CAP * sent
    lineup = min(max(LINEUP_PER_WEEK * gain_per_week, -cap), cap)
    out = sent + max(0.0, -bonus)
    margin = SHARK_MARGIN * out if persona == "shark" else 0.0
    weight = LINEUP_WEIGHT if lineup > 0 else LINEUP_LOSS_WEIGHT
    score = received + max(0.0, bonus) + spot + weight * lineup - out * multiple * OUT_MARGIN - margin
    return Tally(round(sent, 2), round(received, 2), round(bonus, 2), spot, round(lineup, 2), round(score, 2))


def market_ok(know: Knowledge, sends: Sequence[int], kept: Sequence[int], drops: Sequence[int]) -> bool:
    """The anti-stupid guard: on neutral chart values it gets back at least MARKET_GUARD of what it sends."""
    chart = know.by_id
    got = sum(chart[pid].chart for pid in kept) - sum(chart[pid].chart for pid in drops)
    return got >= MARKET_GUARD * sum(chart[pid].chart for pid in sends)
