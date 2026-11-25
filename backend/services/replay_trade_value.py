"""Replay League trade scoring: one CPU's net on the 1000 chart scale for a deal (`tally`).

score = in + premium + spot + 0.5 x lineup + need - out x multiple x 1.03 (a shark also wants SHARK_MARGIN x out):
- prices: `replay_styles.price` for its `Mind` (the persona's private value, an emotional CPU's `hype`, a fan's
  team, a contender's starters, and on what it sends its feelings: worried caps the price at the chart, "high
  on" asks a mark-up, `replay_styles.markup`, whoever buys). A bare persona name prices with no state.
- out: what it sends; an out player it sends the user counts at least
  OUT_SELL_SHARE of his healthy chart (the user may know he is back soon). A player the CPU is shopping to
  the user (`markdown`, replay_mood) counts that share less when sent to the user, before the floor.
- in: what it receives and keeps, minus its own players it must cut to make room. Depth is worth less than
  quality: when it keeps more players than it sends pieces that matter (worth >= DEPTH_SHARE x its best
  piece in the deal, so a junk throw-in asked of it changes nothing), its least valuable pieces beyond the
  first extra one count at DEPTH_SHARE (`depth_counted`), so a pile of bench players never buys a star.
- premium: the best player in the deal is worth more than parts: rate x (B - v2), B the best chart in the
  deal and v2 the other side's best. Signed: + when the CPU gets B, - when it sends him.
- spot: SPOT_VALUE per roster spot the deal frees.
- lineup: LINEUP_PER_WEEK x its lineup gain a week, clamped to +- LINEUP_CAP x out.
- need: `replay_styles.need_bonus` for players at positions it needs, cut so that it and the weighted lineup
  gain together add at most replay_styles.NEED_CAP x out.
The multiple is 1.00 (normal) or 1.08 (hard) against the user and 0.95 between CPUs; for a CPU's own offer to
the user it is 1 / ((1 + its persona's top edge) x 1.03), with no shark margin (replay_trades.offer_multiple):
it pays up to that edge for a player it wants. A CPU accepts at score >= 0 when `market_ok` (the anti-stupid
guard on neutral chart values, with the same depth discount, whatever the persona) holds too.
Just picked up (`fresh`, from `replay_state.pickups`): a player his team added this week and worth
under PICKUP_CHART_MAX counts PICKUP_SHARE of his value everywhere a trade is priced (both sides, the guard, the
premium and the user's verdict), so a free-agent pickup can't be flipped for value the week he arrives.
Pure: takes a Knowledge and the players, no state.
"""
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass

from services import replay_styles
from services.replay_styles import Mind
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
DEPTH_SHARE = 0.35             # a piece beyond the first extra one in a many-for-few deal counts at this share
PICKUP_CHART_MAX = 100.0       # a player added this week and worth less than this (about the top 100)...
PICKUP_SHARE = 0.5             # ...counts this share of his value in trades until the next week


@dataclass(frozen=True)
class Tally:
    sent: float                # its private value of what it sends (sell floor included)
    received: float            # of what it keeps, minus its forced drops
    premium: float             # signed: + it gets the best player in the deal, - it sends him
    spot: float
    lineup: float              # before LINEUP_WEIGHT / LINEUP_LOSS_WEIGHT
    score: float               # accept at >= 0
    need: float = 0.0          # its need bonus, after the cap
    bare_score: float = 0.0    # the score without its "high on" mark-ups (== score when it has none)


def fresh(know: Knowledge, picked: Iterable[int]) -> frozenset[int]:
    """Of the players picked up this week (`replay_state.pickups`), those who count PICKUP_SHARE."""
    return frozenset(pid for pid in picked if pid in know.by_id and know.by_id[pid].chart < PICKUP_CHART_MAX)


def chart(know: Knowledge, player_id: int, just_added: Collection[int] = ()) -> float:
    """His chart value in a trade: PICKUP_SHARE of it when he is in `just_added` (from `fresh`)."""
    value = know.by_id[player_id].chart
    return value * PICKUP_SHARE if player_id in just_added else value


def _best(know: Knowledge, player_ids: Sequence[int], just_added: Collection[int]) -> float:
    return max((chart(know, pid, just_added) for pid in player_ids), default=0.0)


def premium(know: Knowledge, persona: str, sends: Sequence[int], kept: Sequence[int],
            just_added: Collection[int] = ()) -> float:
    """rate x (B - v2), + when the best player in the deal is among `kept`, - when among `sends`."""
    rate = GAMBLER_PREMIUM if persona == "gambler" else STAR_PREMIUM
    return rate * (_best(know, kept, just_added) - _best(know, sends, just_added))


def depth_counted(worth: Sequence[float], sent: Sequence[float]) -> float:
    """The sum of `worth` (what a team keeps), every piece past n + 1 at DEPTH_SHARE (smallest first), n the
    pieces in `sent` worth at least DEPTH_SHARE x the best of them."""
    best = max(sent, default=0.0)
    matter = sum(w >= DEPTH_SHARE * best for w in sent)
    ordered = sorted(worth, reverse=True)
    return sum(w if n <= matter else DEPTH_SHARE * w for n, w in enumerate(ordered))


def tally(know: Knowledge, persona: str | Mind, sends: Sequence[int], kept: Sequence[int], drops: Sequence[int],
          gain_per_week: float, multiple: float, to_user: bool, hype: float = 1.0,
          markdown: Mapping[int, float] | None = None, just_added: Collection[int] = (),
          offered: bool = False) -> Tally:
    """The CPU sends `sends`, keeps `kept` of what it receives and cuts `drops` (its own players).
    `persona`: its Mind (replay_styles.mind_for), or a persona name priced with `hype` and no state.
    `markdown`: {player id: discount} it is shopping to the user (only applied when `to_user`); `just_added`:
    players who count PICKUP_SHARE (`fresh`); `offered`: its own offer to the user (no shark margin)."""
    mind = persona if isinstance(persona, Mind) else replay_styles.plain(persona, hype)
    cut = (markdown or {}) if to_user else {}

    def private(pid: int, own: bool = False, attach: bool = True) -> float:
        value = replay_styles.price(know, mind, pid, own, attach)
        return value * PICKUP_SHARE if pid in just_added else value

    def sell(pid: int, off: float = 0.0, attach: bool = True) -> float:
        value = know.by_id[pid]
        floor = OUT_SELL_SHARE * value.healthy_chart if to_user and value.out else 0.0
        return max(private(pid, True, attach) * (1.0 - off), floor)

    kept_worth = {pid: private(pid) for pid in kept}
    received = (depth_counted(list(kept_worth.values()), [private(pid, True) for pid in sends])
                - sum(private(pid, True) for pid in drops))
    bonus = premium(know, mind.persona, sends, kept, just_added)
    spot = SPOT_VALUE * max(0, len(sends) - len(kept))
    cap = LINEUP_CAP * sum(sell(pid) for pid in sends)     # undiscounted: a shop moves his price by its discount only
    lineup = min(max(LINEUP_PER_WEEK * gain_per_week, -cap), cap)
    weight = LINEUP_WEIGHT if lineup > 0 else LINEUP_LOSS_WEIGHT

    def score_for(attach: bool) -> tuple[float, float, float]:
        sent = sum(sell(pid, cut.get(pid, 0.0), attach) for pid in sends)
        out = sent + max(0.0, -bonus)
        room = max(0.0, replay_styles.NEED_CAP * out - max(0.0, weight * lineup))
        need = min(replay_styles.need_bonus(know, mind, sends, kept, kept_worth), room)
        margin = SHARK_MARGIN * out if mind.persona == "shark" and not offered else 0.0
        score = received + max(0.0, bonus) + spot + weight * lineup + need - out * multiple * OUT_MARGIN - margin
        return sent, need, score

    sent, need, score = score_for(True)
    bare = score_for(False)[2] if any(replay_styles.markup(know, mind, pid) for pid in sends) else score
    return Tally(round(sent, 2), round(received, 2), round(bonus, 2), spot, round(lineup, 2), round(score, 2),
                 round(need, 2), round(bare, 2))


def market_ok(know: Knowledge, sends: Sequence[int], kept: Sequence[int], drops: Sequence[int],
              just_added: Collection[int] = ()) -> bool:
    """The anti-stupid guard: on neutral chart values it gets back at least MARKET_GUARD of what it sends."""
    def worth(ids: Sequence[int]) -> list[float]:
        return [chart(know, pid, just_added) for pid in ids]

    got = depth_counted(worth(kept), worth(sends)) - sum(worth(drops))
    return got >= MARKET_GUARD * sum(worth(sends))
