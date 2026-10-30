"""What to do about each waiver target in YOUR league, by how the league runs waivers.

Every candidate is scored by lineup gain G (services/lineup_gain.py) with a named drop, then:

FAAB leagues (Yahoo uses_faab):
    market P = median over weeks of the k-th highest winning bid (% of the budget) x
               (average rival balance / budget), k = the player's demand rank this week
    demand   = clamp(1 + 0.15 x (rivals who need his position and can afford P
                                - the median of that count across positions), 0.7, 1.5)
    worth    = G x your balance / (4 x weeks left)      (a full budget buys ~4 lineup points a week)
    bid      = min(max(P x demand, minimum bid), worth, richest interested rival + $1, half your balance)
    If P x demand is more than he's worth to you: pass. With fewer than MIN_BID_HISTORY winning
    bids, P comes from a default table by demand rank (rank 1 15%, 2 8%, 3 5%, 4-6 3%, else 1%),
    x1.3 in weeks 1-4, x1.0 in weeks 5-10, x0.6 from week 11, and is labelled an estimate.
Rolling waiver priority (no FAAB): spend a top-3 priority only on a real difference-maker:
    G >= BIG_GAIN and the most-wanted player, a handcuff to your own Out/IR starter, or a player
    who'd start for you in 3+ of the next 4 weeks. Otherwise wait: he'll likely clear waivers.
    Teams ahead of you that need his position are named (your claim may lose to them).
No waivers (free agency only): add now, best gain first.

Demand rank is the candidate's rank by rest-of-season value among this week's candidates (the
market's view, since other managers judge by value, not by your roster).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from statistics import median

MIN_BID_HISTORY = 8
DEFAULT_PRICE = {1: 15.0, 2: 8.0, 3: 5.0, 4: 3.0, 5: 3.0, 6: 3.0}
DEFAULT_PRICE_OTHER = 1.0
DEMAND_STEP = 0.15
DEMAND_LIMITS = (0.7, 1.5)
POINTS_PER_BUDGET_WEEK = 4.0
MAX_BALANCE_SHARE = 0.5
BIG_GAIN = 8.0
TOP_PRIORITY = 3
STARTER_WEEKS = 3
MIN_USEFUL_GAIN = 1.0          # under this many lineup points the move isn't worth a roster spot


@dataclass(frozen=True)
class Advice:
    action: str                     # bid | pass | claim | wait | add
    note: str
    bid: int | None = None
    market: int | None = None
    worth: int | None = None
    estimate: bool = False

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def league_type(rules: Mapping) -> str:
    if rules.get("uses_faab"):
        return "faab"
    if rules.get("waiver_type") in (None, "", "N", "none"):
        return "free_agency"
    return "priority"


def _phase(week: int) -> float:
    return 1.3 if week <= 4 else 1.0 if week <= 10 else 0.6


def market_price(rank: int, winning_bids_by_week: Sequence[Sequence[float]], budget: float,
                 rival_balances: Sequence[float], week: int) -> tuple[float, bool]:
    """(price as % of budget, is_estimate) for the rank-th most wanted player."""
    history = [sorted(bids, reverse=True) for bids in winning_bids_by_week if bids]
    if sum(len(b) for b in history) >= MIN_BID_HISTORY:
        kth = [bids[rank - 1] for bids in history if len(bids) >= rank]
        if kth:
            spend = (sum(rival_balances) / len(rival_balances) / budget) if rival_balances and budget else 1.0
            return median(kth) / budget * 100 * spend, False
    return DEFAULT_PRICE.get(rank, DEFAULT_PRICE_OTHER) * _phase(week), True


def faab_advice(gain: float, rank: int, position: str, balance: float, budget: float, weeks_left: int,
                rivals: Sequence[Mapping], winning_bids_by_week: Sequence[Sequence[float]], week: int,
                min_bid: int = 0) -> Advice:
    rival_balances = [r["balance"] for r in rivals if r.get("balance") is not None]
    price_pct, estimate = market_price(rank, winning_bids_by_week, budget, rival_balances, week)
    price = price_pct / 100 * budget
    counts = {pos: sum(1 for r in rivals if pos in r.get("needs", ()) and (r.get("balance") or 0) >= price)
              for pos in ("QB", "RB", "WR", "TE")}
    demand = max(DEMAND_LIMITS[0], min(DEMAND_LIMITS[1],
                                       1 + DEMAND_STEP * (counts.get(position, 0) - median(counts.values()))))
    worth = gain * balance / (POINTS_PER_BUDGET_WEEK * max(weeks_left, 1))
    market = price * demand
    interested = [r["balance"] for r in rivals if position in r.get("needs", ()) and r.get("balance") is not None]
    if gain < MIN_USEFUL_GAIN:
        return Advice("pass", _marginal(gain), market=round(market), worth=round(worth), estimate=estimate)
    if market > worth:
        return Advice("pass", f"Pass: the market is about ${round(market)}, he's worth about ${round(worth)} to you.",
                      market=round(market), worth=round(worth), estimate=estimate)
    caps = [worth, MAX_BALANCE_SHARE * balance] + ([max(interested) + 1] if interested else [])
    bid = max(min_bid, min(max(market, min_bid), *caps))
    label = " (estimate: not enough bids in your league yet)" if estimate else ""
    return Advice("bid", f"Bid ${round(bid)}: market about ${round(market)}{label}, worth about ${round(worth)} to you.",
                  bid=round(bid), market=round(market), worth=round(worth), estimate=estimate)


def priority_advice(gain: float, rank: int, position: str, my_priority: int | None,
                    teams_ahead_needing: Sequence[str], is_handcuff_to_injured: bool, starts_weeks: int) -> Advice:
    ahead = f" {', '.join(teams_ahead_needing[:3])} (ahead of you) need {position}." if teams_ahead_needing else ""
    if gain < MIN_USEFUL_GAIN:
        return Advice("wait", _marginal(gain))
    worth_top = (gain >= BIG_GAIN and rank == 1) or is_handcuff_to_injured or starts_weeks >= STARTER_WEEKS
    if my_priority is not None and my_priority <= TOP_PRIORITY and not worth_top:
        return Advice("wait", f"Wait: not worth your #{my_priority} priority; he'll likely clear waivers.{ahead}")
    why = ("a handcuff to your injured starter" if is_handcuff_to_injured
           else f"he'd start {starts_weeks} of the next 4 weeks" if starts_weeks >= STARTER_WEEKS
           else f"+{gain:.1f} lineup points")
    priority = f" with your #{my_priority} priority" if my_priority else ""
    return Advice("claim", f"Claim{priority}: {why}.{ahead}")


def free_agent_advice(gain: float) -> Advice:
    return Advice("add", f"Add now: +{gain:.1f} lineup points.") if gain >= MIN_USEFUL_GAIN else Advice("wait", _marginal(gain))


def _marginal(gain: float) -> str:
    return "Not an upgrade on your current roster." if gain <= 0 else f"Marginal: only +{gain:.1f} lineup points."
