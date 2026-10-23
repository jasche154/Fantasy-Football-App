"""Trade finder: offers built around the players you're willing to move.

For each of your players (and each pair, for 2-for-1 consolidation) it finds players on
other teams whose rest-of-season value makes a deal the other manager should accept:
- the evaluated verdict is fair or a slight edge either way;
- you improve, or at least break even, at the position you want.

Offers are ranked by your value gain, then by the incoming player's weekly scoring rate.
"""
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import combinations

from services.trade import TradeEvaluation, evaluate_trade, package, percent_difference, verdict_for
from services.valuation import PlayerValue, Valuation

ACCEPTABLE = {"fair", "slight_win", "slight_loss"}
MAX_PERCENT_GAIN = 0.15          # gains beyond this rarely get accepted
MIN_TARGET_VALUE = 1.0
PAIR_POOL = 6                    # 2-for-1 packages use your six most valuable players


@dataclass(frozen=True)
class TradeOffer:
    give: tuple[PlayerValue, ...]
    get: PlayerValue
    evaluation: TradeEvaluation


def find_trades(valuation: Valuation, my_ids: Sequence[int], want_position: str | None = None,
                limit: int = 10) -> list[TradeOffer]:
    mine = [valuation.by_id[i] for i in dict.fromkeys(my_ids) if i in valuation.by_id]   # de-duplicated
    if not mine:
        return []
    my_set = {p.player_id for p in mine}
    targets = [v for v in valuation.values if v.player_id not in my_set and v.trade_value >= MIN_TARGET_VALUE
               and (want_position is None or v.position == want_position)]
    top = sorted(mine, key=lambda p: -p.trade_value)[:PAIR_POOL]
    packages = [(p,) for p in mine] + list(combinations(top, 2))
    shortlist = []
    for give in packages:
        give_total = package(give).adjusted_total
        for target in targets:
            if len(give) == 2 and target.trade_value <= max(p.trade_value for p in give):
                continue                                   # 2-for-1 should consolidate into a better player
            percent = percent_difference(give_total, package([target]).adjusted_total)
            if verdict_for(percent) in ACCEPTABLE and -0.05 <= percent <= MAX_PERCENT_GAIN:
                shortlist.append((percent, give, target))
    shortlist.sort(key=lambda item: (-item[0], -item[2].ros_ppg))
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
