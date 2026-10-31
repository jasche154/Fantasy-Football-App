"""Trade finder: offers built around the players you're willing to move.

For each of your players (and each pair, for 2-for-1 consolidation) it finds players on
other teams whose rest-of-season value makes a deal the other manager should accept:
- by this app's values, you lose at most FAIR_THRESHOLD and gain at most MAX_PERCENT_GAIN;
- by market value (the expert-consensus ROS rank, which is how most managers judge a deal),
  the other side doesn't lose more than MAX_MARKET_EDGE (a 2-for-1 must clear the
  consolidation discount in market terms too, since the stud's owner has to want the depth),
  and you don't give up more than -MIN_MARKET_EDGE (you could ask for more);
- the app likes the deal for you at least as much as the market does: the edge comes from
  where this app's projection differs from consensus, never from hoping they misread it.

Offers are ranked by expected edge: your edge by this app's values x the chance the other
manager accepts (a logistic in how the market sees the deal for them). Offers with no edge
for you follow, most likely to be accepted first.
"""
import math
from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from itertools import combinations

from services.trade import FAIR_THRESHOLD, TradeEvaluation, evaluate_trade, package, percent_difference
from services.valuation import PlayerValue, Valuation, expected_vorp

MAX_PERCENT_GAIN = 0.08          # by this app's values; bigger edges rarely get accepted
MIN_PERCENT_GAIN = -FAIR_THRESHOLD   # a fair deal for you can still be a small loss by these values
MAX_MARKET_EDGE = 0.05           # by market (expert) value, the most the other side gives up
MIN_MARKET_EDGE = -0.15          # and the most you give up: past this, you could ask for more
# Chance they accept = 1 / (1 + exp(-(their market edge - ACCEPT_CENTER) / ACCEPT_SCALE)):
# about 62% for a deal the market calls even, 32% for one it calls a 5% loss for them.
ACCEPT_CENTER = -0.02
ACCEPT_SCALE = 0.04
MIN_TARGET_VALUE = 1.0
PAIR_POOL = 6                    # 2-for-1 packages use your six most valuable players


@dataclass(frozen=True)
class TradeOffer:
    give: tuple[PlayerValue, ...]
    get: PlayerValue
    evaluation: TradeEvaluation
    market_percent: float = 0.0          # the deal for you by market value (+ = you win)
    accept_chance: float = 0.0           # rough chance the other manager accepts
    their_drop: PlayerValue | None = None   # league trades: who they drop to fit a 2-for-1
    my_add: PlayerValue | None = None       # league trades: the free agent for the spot you open


def market_value(player: PlayerValue, valuation: Valuation) -> PlayerValue:
    """The player as the market sees him: expert ROS points (when ranked) above replacement."""
    if player.expert_ros_ppg is None:
        return player
    ppg = player.expert_ros_ppg * player.availability
    vorp = expected_vorp(ppg, player.availability, valuation.replacement.get(player.position or "", 0.0))
    games = player.games_left or valuation.weeks_remaining
    return replace(player, ros_ppg=round(ppg, 2), vorp_per_game=round(vorp, 2), trade_value=round(vorp * games, 1))


def market_percent(give: Sequence[PlayerValue], get: Sequence[PlayerValue], valuation: Valuation) -> float:
    """The deal for you (+ = you win) by market value, with the same package discount."""
    return percent_difference(package([market_value(p, valuation) for p in give]).adjusted_total,
                              package([market_value(p, valuation) for p in get]).adjusted_total)


def accept_chance(market_edge_for_you: float) -> float:
    """Rough chance the other manager accepts, from how the market sees the deal for them."""
    theirs = -market_edge_for_you
    return round(1 / (1 + math.exp(-(theirs - ACCEPT_CENTER) / ACCEPT_SCALE)), 3)


def _rank_key(percent: float, chance: float, target: PlayerValue) -> tuple:
    return (-(max(percent, 0.0) * chance), -chance, -target.ros_ppg)


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
            if not MIN_PERCENT_GAIN <= percent <= MAX_PERCENT_GAIN:
                continue
            market = market_percent(give, [target], valuation)
            if not MIN_MARKET_EDGE <= market <= MAX_MARKET_EDGE or percent < market:
                continue                                   # lopsided to one side, or no edge from the model
            shortlist.append((percent, market, give, target))
    shortlist.sort(key=lambda item: _rank_key(item[0], accept_chance(item[1]), item[3]))
    seen, offers = set(), []
    for _, market, give, target in shortlist:              # one offer per incoming player
        if target.player_id in seen:
            continue
        seen.add(target.player_id)
        offers.append(TradeOffer(give, target, evaluate_trade(list(give), [target], valuation.values,
                                                              valuation.weeks_remaining),
                                 market_percent=market, accept_chance=accept_chance(market)))
        if len(offers) == limit:
            break
    return offers
