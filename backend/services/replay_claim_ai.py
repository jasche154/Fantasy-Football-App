"""Replay League: the CPU's "hot" waiver route and its FAAB bids (used by services/replay_waiver_ai.cpu_claims).

`ros_ppg` lags a breakout: after one big game the ADP prior still fills three of its four weighted slots, so
a player like Puka Nacua in week 2 of 2023 sits below replacement and adds nothing to a lineup. So at the
waiver run a CPU also looks at his real points from weeks already played (`ReplayValue.recent_points`, built
from `data.as_of(week)`, never this week or later):

- hot (`is_hot`): a skill player, not out, who played within the last HOT_RECENT_WEEKS weeks and whose mean
  over his last HOT_GAMES games beats his ros_ppg by HOT_MIN_GAP or more.
- hot rate: HOT_WEIGHT x that mean + the rest x ros_ppg. `HotProjector` counts a hot player at that rate for
  the weeks after this one (this week's projection is unchanged), for every CPU, its own players included.
- Up to MAX_HOT hot free agents join the claim short list (biggest gap first), and one is claimed for
  HOT_CLAIM_GAIN_PER_WEEK lineup points per weighted week left instead of the usual bar.

FAAB (`cpu_bid`): a share of the budget left that grows with the gain per weighted week and as the season's
waiver runs run out (`pace`), with seeded noise; at most MAX_BID_SHARE of it while more than LATE_RUNS runs
remain. Each of a CPU's claims is bid against the same budget left (blind bids, like the user's).
"""
from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType, SimpleNamespace

from services import replay_pool, replay_state as rs, replay_values as rv
from services.replay_pool import SKILL
from services.replay_values import Knowledge, ReplayValue

HOT_GAMES = 2                     # his last games that make the hot mean
HOT_WEIGHT = 0.5                  # the hot mean's share of the hot rate (ros_ppg has the rest)
HOT_MIN_GAP = 6.0                 # points per game the hot mean must beat ros_ppg by
HOT_RECENT_WEEKS = 2              # his last game no longer ago than this many weeks
MAX_HOT = 6                       # hot free agents added to the claim short list
HOT_CLAIM_GAIN_PER_WEEK = 0.5     # the claim bar for a hot player (replay_waiver_ai.CLAIM_GAIN_PER_WEEK: 1.0)

BID_PER_POINT = 0.08              # share of the budget left per lineup point a (weighted) week
MAX_BID_SHARE = 0.4               # the most of what is left a CPU bids while more than LATE_RUNS runs remain
LATE_RUNS = 4
PACE_RUNS = 8                     # bids grow once fewer than this many runs are left
BID_NOISE = 0.2                   # bids vary by up to this share either way (seeded)


def _hot_mean(value: ReplayValue) -> float:
    recent = value.recent_points[:HOT_GAMES]
    return sum(recent) / len(recent)


def hot_gap(value: ReplayValue) -> float:
    """How far his recent games beat his rest-of-season rate (0 without a game)."""
    return _hot_mean(value) - value.ros_ppg if value.recent_points else 0.0


def hot_rate(value: ReplayValue) -> float:
    return HOT_WEIGHT * _hot_mean(value) + (1 - HOT_WEIGHT) * value.ros_ppg


def is_hot(know: Knowledge, value: ReplayValue) -> bool:
    return (value.position in SKILL and not value.out and bool(value.recent_points)
            and value.last_game_week >= know.week - HOT_RECENT_WEEKS and hot_gap(value) >= HOT_MIN_GAP)


@lru_cache(maxsize=64)
def hot_rates(know: Knowledge) -> Mapping[int, float]:
    """{player id: hot rate} for every hot player, rostered or not."""
    return MappingProxyType({v.player_id: hot_rate(v) for v in know.values if is_hot(know, v)})


class HotProjector(rv.ReplayProjector):
    """replay_values' projector with hot players at their hot rate after this week. Build a new one per use:
    it caches points by (player, week), so it must never share a cache with replay_values.projector."""

    def __init__(self, know: Knowledge, rates: Mapping[int, float]):
        weekly = {v.player_id: SimpleNamespace(projection=v.projection) for v in know.values}
        super().__init__(know, {}, weekly, max(1, know.week))
        self.rates = rates

    def healthy_rate(self, value) -> float:
        return self.rates.get(value.player_id, value.ros_ppg)


replay_pool.EVICT_HOOKS.append(hot_rates.cache_clear)


def hot_candidates(know: Knowledge, available: list[ReplayValue], listed: list[ReplayValue]) -> list[ReplayValue]:
    """Up to MAX_HOT hot players from `available` not already `listed`, biggest gap first (ties: lower id)."""
    seen = {v.player_id for v in listed}
    hot = [v for v in available if v.player_id not in seen and is_hot(know, v)]
    return sorted(hot, key=lambda v: (-hot_gap(v), v.player_id))[:MAX_HOT]


def cpu_bid(gain: float, weights: float, left: int, runs_left: int, dice) -> int:
    """Whole dollars a CPU bids for a claim worth `gain` season-weighted lineup points (`weights`: the sum of
    the horizon's week weights) with `left` dollars and `runs_left` waiver runs to go (this one included)."""
    pace = max(1.0, PACE_RUNS / max(1, runs_left))
    cap = 1.0 if runs_left <= LATE_RUNS else MAX_BID_SHARE
    share = min(cap, BID_PER_POINT * gain / weights * pace * dice.uniform(1 - BID_NOISE, 1 + BID_NOISE))
    return max(0, min(left, int(left * share)))


def claim_bid(state: rs.State, settings: rs.Settings, seed: int, team_id: int, add_id: int, gain: float,
              weights: float) -> int:
    """A CPU's bid for one claim: `cpu_bid` in a FAAB league, else 0."""
    if settings.waiver_mode != "faab":
        return 0
    dice = rs.rng(seed, "faab", state.week, team_id, add_id)
    return cpu_bid(gain, weights, rs.faab_left(state, team_id), settings.end_week - state.week + 1, dice)
