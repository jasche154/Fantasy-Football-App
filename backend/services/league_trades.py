"""Trades in YOUR league, scored by both starting lineups' gains (services/lineup_gain.py).

One scorer serves the league trade list (score_trades) and the calculator (trade_impact), so a
trade shows the same gains in both places:
- your gain uses your playoff odds; theirs uses neutral playoff weights (we don't know theirs);
- roster spots count: when a side gets more players than it sends and its roster would be over
  capacity, it drops the player it misses least (one of its DROP_CANDIDATES lowest-rated); when
  you send more than you get and your roster was full, the spot you open is filled by the best
  of the top FREE_AGENT_POOL free agents. Both are named on the offer and included in the gains.
"""
from collections.abc import Sequence
from dataclasses import replace
from datetime import date

from sqlalchemy.orm import Session

from database.models import Team
from services import lineup_gain
from services import my_team as mt
from services.playoffs import playoff_weeks

TRADE_GIVE_LIMIT = 0.5            # their lineup may lose at most this x weighted weeks left
THEIR_LOSS_PENALTY = 0.5          # offer_score: each point their lineup loses costs half a point of rank
DROP_CANDIDATES = 4               # a roster over capacity drops one of its four lowest-rated players
FREE_AGENT_POOL = 10              # free agents considered for the spot an uneven trade opens
MAX_SHARED_GIVE = 2               # at most two offers per team built on the same player of yours


def capacity(settings) -> int:
    return sum(settings.starters.values()) + settings.bench


def active_spots(spots: Sequence) -> int:
    return sum(1 for s in spots if s.slot not in mt.INACTIVE_SLOTS)


def deadline_passed(trade_end_date: str | None, today: date | None = None) -> bool:
    """True once the league's trade deadline (YYYY-MM-DD, trades allowed through that day) is behind us."""
    if not trade_end_date:
        return False
    try:
        deadline = date.fromisoformat(str(trade_end_date))
    except ValueError:
        return False
    return (today or date.today()) > deadline


def offer_score(my_gain: float, their_gain: float) -> float:
    """Rank by your gain, less part of what the deal costs them (deals that hurt them get turned down)."""
    return my_gain + THEIR_LOSS_PENALTY * min(their_gain, 0.0)


def _forced_drop(roster: Sequence, horizon, projector, settings, is_over: bool) -> tuple[list, object | None]:
    """(roster, dropped player): a roster over capacity drops the player it misses least."""
    if not is_over or not roster:
        return list(roster), None
    candidates = sorted(roster, key=lambda v: v.ros_ppg)[:DROP_CANDIDATES]
    best = max(candidates, key=lambda d: (lineup_gain.season_points([v for v in roster if v.player_id != d.player_id],
                                                                      horizon, projector, settings), -d.ros_ppg))
    return [v for v in roster if v.player_id != best.player_id], best


def _distinct(scored: Sequence[tuple]) -> list[tuple]:
    """At most MAX_SHARED_GIVE offers built on the same player of yours, so a team's list isn't one idea."""
    used: dict[int, int] = {}
    out = []
    for item in scored:
        ids = [p.player_id for p in item[0].give]
        if any(used.get(i, 0) >= MAX_SHARED_GIVE for i in ids):
            continue
        for i in ids:
            used[i] = used.get(i, 0) + 1
        out.append(item)
    return out


class TradeScorer:
    """Both lineups' gains for trades in one league (built once per request)."""

    def __init__(self, session: Session, league_id: int):
        self.session, self.league_id = session, league_id
        self.team = mt.build_my_team(session, league_id)
        self.horizon, self.projector = lineup_gain.context_for(self.team)
        self.neutral = lineup_gain.horizon(self.horizon.current_week, max(self.horizon.weeks),
                                           playoff_weeks(mt.league_rules(self.team.league)), None)
        self.settings = self.team.settings
        self.mine = lineup_gain.roster_values(self.team)
        self.capacity = capacity(self.settings)
        self.my_full = active_spots(self.team.spots) >= self.capacity
        self.my_base = self._points(self.mine, self.horizon)
        self.rosters = mt.rosters(session, self.team.league)
        self.floor = -TRADE_GIVE_LIMIT * sum(self.neutral.weights.values())
        self._free_agents: list | None = None
        self._theirs: dict[int, tuple[list, float, int]] = {}

    def _points(self, roster: Sequence, horizon) -> float:
        return lineup_gain.season_points(roster, horizon, self.projector, self.settings)

    def free_agents(self) -> list:
        if self._free_agents is None:
            try:
                _, _, candidates = mt.league_waivers(self.session, self.league_id, None, FREE_AGENT_POOL)
            except LookupError:
                candidates = []
            values = (self.team.valuation.by_id.get(c.player.player_id) for c in candidates)
            self._free_agents = [v for v in values if v is not None and v.position in mt.MODELED]
        return self._free_agents

    def theirs(self, team_id: int) -> tuple[list, float, int]:
        """(roster values, season points, active spots) of another team."""
        if team_id not in self._theirs:
            spots = self.rosters.get(team_id, [])
            roster = lineup_gain.roster_values(self.team, spots)
            self._theirs[team_id] = (roster, self._points(roster, self.neutral), active_spots(spots))
        return self._theirs[team_id]

    def _open_spot_add(self, roster: Sequence, base: float) -> tuple[float, object | None]:
        scored = [(self._points([*roster, fa], self.horizon) - base, fa) for fa in self.free_agents()]
        best = max(scored, key=lambda s: s[0], default=(0.0, None))
        return best if best[0] > 0 else (0.0, None)

    def score(self, give: Sequence, get: Sequence, their_team_id: int | None) -> dict:
        """{your_gain, their_gain (None without a team), their_drop, my_add} for give -> get."""
        give_ids, get_ids = {p.player_id for p in give}, {p.player_id for p in get}
        my_after = [v for v in self.mine if v.player_id not in give_ids] + list(get)
        after = self._points(my_after, self.horizon)
        my_gain, my_add = after - self.my_base, None
        if len(give) > len(get) and self.my_full:
            bonus, my_add = self._open_spot_add(my_after, after)
            my_gain += bonus
        their_gain, their_drop = None, None
        if their_team_id is not None:
            roster, base, spots = self.theirs(their_team_id)
            their_after = [v for v in roster if v.player_id not in get_ids] + list(give)
            is_over = len(give) > len(get) and spots + len(give) - len(get) > self.capacity
            their_after, their_drop = _forced_drop(their_after, self.neutral, self.projector, self.settings, is_over)
            their_gain = round(self._points(their_after, self.neutral) - base, 2)
        return {"your_gain": round(my_gain, 2), "their_gain": their_gain, "their_drop": their_drop, "my_add": my_add}


def score_trades(session: Session, league_id: int, by_team: Sequence) -> list[tuple]:
    """[(team offers, [(offer, my_gain, their_gain)])], best first: candidate offers scored by both lineups.

    Keeps offers that help you (my_gain > 0) without costing them more than TRADE_GIVE_LIMIT points per
    weighted week left, ranked by offer_score, at most OFFERS_PER_TEAM per team. The named drop and
    free-agent add ride on the offer (offer.their_drop, offer.my_add).
    """
    scorer = TradeScorer(session, league_id)
    out = []
    for team_offers in by_team:
        scored = []
        for offer in team_offers.offers:
            result = scorer.score(offer.give, [offer.get], team_offers.team.id)
            mine, theirs = result["your_gain"], result["their_gain"]
            if mine > 0 and theirs >= scorer.floor:
                scored.append((replace(offer, their_drop=result["their_drop"], my_add=result["my_add"]), mine, theirs))
        if scored:
            scored.sort(key=lambda s: -offer_score(s[1], s[2]))
            out.append((team_offers, _distinct(scored)[:mt.OFFERS_PER_TEAM]))
    return sorted(out, key=lambda t: -offer_score(t[1][0][1], t[1][0][2]))


def trade_impact(session: Session, league_id: int, give_ids: Sequence[int], get_ids: Sequence[int]) -> dict:
    """A calculator trade in your league: both lineups' gains (as on the league trade list) and the
    rosters balance suggestions may draw on.

    The other side is the team that rosters every player in get. Without one (players spread over
    teams or free agents) only your gain is scored. When give isn't all yours, or get includes your
    own players, no gains are scored and `note` says why: the value verdict still stands.
    """
    scorer = TradeScorer(session, league_id)
    my_ids = {s.player_id for s in scorer.team.spots}
    owner = next((tid for tid, spots in scorer.rosters.items() if tid != scorer.team.team.id
                  and set(get_ids) <= {s.player_id for s in spots}), None)
    pool = my_ids | {s.player_id for s in scorer.rosters.get(owner, [])}
    note = None
    if any(pid not in my_ids for pid in give_ids):
        note = "Some players you give aren't on your roster in this league, so lineup gains aren't scored."
    elif any(pid in my_ids for pid in get_ids):
        note = "Some players you get are already on your roster, so lineup gains aren't scored."
    if note:
        return {"your_gain": None, "their_gain": None, "their_team": None, "their_drop": None,
                "your_add": None, "note": note, "pool_ids": pool}
    by_id = scorer.team.valuation.by_id
    result = scorer.score([by_id[p] for p in give_ids if p in by_id], [by_id[p] for p in get_ids if p in by_id], owner)
    their_team = session.get(Team, owner) if owner is not None else None
    return {"your_gain": result["your_gain"], "their_gain": result["their_gain"],
            "their_team": their_team.name if their_team else None,
            "their_drop": result["their_drop"], "your_add": result["my_add"], "note": None, "pool_ids": pool}
