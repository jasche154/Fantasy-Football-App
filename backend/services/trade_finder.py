"""Trade finder: offers built around the players you're willing to move.

For each of your players (and each pair, for 2-for-1 consolidation) it finds players on
other teams whose rest-of-season value makes a deal the other manager should accept:
- by this app's values, it's fair or a small edge for you (at most MAX_PERCENT_GAIN);
- by market value (the expert-consensus ROS rank, which is how most managers judge a deal),
  the other side doesn't lose more than MAX_MARKET_EDGE (a 2-for-1 must clear the
  consolidation discount in market terms too, since the stud's owner has to want the depth),
  and you don't give up more than -MIN_MARKET_EDGE (you could ask for more).

Offers are ranked by how close to fair they are (a small edge for you first), then by the
incoming player's rest-of-season scoring rate.
"""
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from itertools import combinations

from dataclasses import replace

from services.trade import TradeEvaluation, evaluate_trade, package, percent_difference, verdict_for
from services.valuation import PlayerValue, Valuation

ACCEPTABLE = {"fair", "slight_win", "slight_loss"}
MAX_PERCENT_GAIN = 0.08          # by this app's values; bigger edges rarely get accepted
MAX_MARKET_EDGE = 0.05           # by market (expert) value, the most the other side gives up
MIN_MARKET_EDGE = -0.15          # and the most you give up: past this, you could ask for more
FAIR_TARGET = 0.03               # the ideal offer: a small edge for you
MIN_TARGET_VALUE = 1.0
PAIR_POOL = 6                    # 2-for-1 packages use your six most valuable players


@dataclass(frozen=True)
class TradeOffer:
    give: tuple[PlayerValue, ...]
    get: PlayerValue
    evaluation: TradeEvaluation


def market_value(player: PlayerValue, valuation: Valuation) -> PlayerValue:
    """The player as the market sees him: expert ROS points (when ranked) above replacement."""
    if player.expert_ros_ppg is None:
        return player
    ppg = player.expert_ros_ppg * player.availability
    vorp = max(0.0, ppg - valuation.replacement.get(player.position or "", 0.0))
    return replace(player, ros_ppg=round(ppg, 2), trade_value=round(vorp * valuation.weeks_remaining, 1))


def _market_percent(give: Sequence[PlayerValue], target: PlayerValue, valuation: Valuation) -> float:
    return percent_difference(package([market_value(p, valuation) for p in give]).adjusted_total,
                              package([market_value(target, valuation)]).adjusted_total)


def find_trades(valuation: Valuation, my_ids: Sequence[int], want_position: str | None = None,
                limit: int = 10, target_ids: Collection[int] | None = None) -> list[TradeOffer]:
    """target_ids limits incoming players (e.g. one real team's roster)."""
    mine = [valuation.by_id[i] for i in dict.fromkeys(my_ids) if i in valuation.by_id]   # de-duplicated
    if not mine:
        return []
    my_set = {p.player_id for p in mine}
    targets = [v for v in valuation.values if v.player_id not in my_set and v.trade_value >= MIN_TARGET_VALUE
               and (want_position is None or v.position == want_position)
               and (target_ids is None or v.player_id in target_ids)]
    top = sorted(mine, key=lambda p: -p.trade_value)[:PAIR_POOL]
    packages = [(p,) for p in mine] + list(combinations(top, 2))
    shortlist = []
    for give in packages:
        give_total = package(give).adjusted_total
        for target in targets:
            if len(give) == 2 and target.trade_value <= max(p.trade_value for p in give):
                continue                                   # 2-for-1 should consolidate into a better player
            percent = percent_difference(give_total, package([target]).adjusted_total)
            if not (verdict_for(percent) in ACCEPTABLE and -0.05 <= percent <= MAX_PERCENT_GAIN):
                continue
            if not MIN_MARKET_EDGE <= _market_percent(give, target, valuation) <= MAX_MARKET_EDGE:
                continue                                   # one side would see it as lopsided
            shortlist.append((percent, give, target))
    shortlist.sort(key=lambda item: (abs(item[0] - FAIR_TARGET), -item[2].ros_ppg))
    seen, offers = set(), []
    for _, give, target in shortlist:                      # one offer per incoming player
        if target.player_id in seen:
            continue
        seen.add(target.player_id)
        offers.append(TradeOffer(give, target, evaluate_trade(list(give), [target], valuation.values,
                                                              valuation.weeks_remaining)))
        if len(offers) == limit:
            break
    return offers
