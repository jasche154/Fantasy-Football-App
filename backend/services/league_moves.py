"""Roster moves in YOUR league, scored by lineup gain: waiver claims with a named drop and the right
advice for the league's waiver type (services/waiver_advice.py), and trade offers that must help
your lineup without gutting theirs.
"""
import logging
from collections.abc import Sequence
from dataclasses import replace

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import Team
from services import league_insights, lineup_gain
from services import my_team as mt
from services import waiver_advice as wa
from services.playoffs import playoff_weeks

CANDIDATE_POOL = 30               # waiver candidates scored by lineup gain (from the value-ranked list)
MAX_SHOWN = CANDIDATE_POOL
TRADE_GIVE_LIMIT = 0.5            # their lineup may lose at most this x weighted weeks left
THEIR_LOSS_PENALTY = 0.5          # offer_score: each point their lineup loses costs half a point of rank
DROP_CANDIDATES = 4               # a roster over capacity drops one of its four lowest-rated players
FREE_AGENT_POOL = 10              # free agents considered for the spot a 2-for-1 opens
MAX_SHARED_GIVE = 2
LOOKAHEAD_WEEKS = 4
HTTP_TIMEOUT_SECONDS = 15.0
log = logging.getLogger(__name__)


def _insights(session: Session, league_id: int) -> dict | None:
    """Standings, balances, priorities and bid history (cached an hour); None if Yahoo is unreachable."""
    from integrations.token_crypto import TokenCryptoError
    from integrations.yahoo import YahooError
    from services.yahoo_sync import YahooNotConfigured

    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            return league_insights.build(session, get_settings(), http, league_id)
    except (httpx.HTTPError, YahooError, YahooNotConfigured, TokenCryptoError, LookupError):
        log.warning("League standings unavailable; waiver advice falls back to estimates", exc_info=True)
        return None


def _roster_full(team) -> bool:
    return _active_spots(team.spots) >= _capacity(team.settings)


def waiver_plan(session: Session, league_id: int, position: str | None = None, limit: int = 15) -> dict:
    team = mt.build_my_team(session, league_id)
    league, rankings, candidates = mt.league_waivers(session, league_id, position, CANDIDATE_POOL)
    horizon, projector = lineup_gain.context_for(team)
    roster = lineup_gain.roster_values(team)
    droppable = roster if _roster_full(team) else []
    protected = lineup_gain.handcuffs(roster, horizon, projector, team.settings)
    rules = mt.league_rules(league)
    kind = wa.league_type(rules)
    insights = _insights(session, league.id) if kind != "free_agency" else None
    me = next((t for t in (insights or {}).get("teams", []) if t.get("is_users_team")), {})
    teams = {t.id: t for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    needs = mt.team_needs(team.valuation, team.settings, mt.rosters(session, league))
    needs_by_name = {teams[tid].name: pos for tid, pos in needs.items() if tid in teams}
    rivals = [{"name": t["team"], "balance": t.get("faab_balance"), "priority": t.get("waiver_priority"),
               "needs": needs_by_name.get(t["team"], ())} for t in (insights or {}).get("teams", [])
              if not t.get("is_users_team")]
    weeks_left = len(horizon.weeks)
    injured_starter_teams = {v.team for v in roster if v.position == "RB"
                             and projector.available(v, 1) < 0.5}
    # Demand rank across every position: filtering to TEs must not make the best TE "rank 1".
    pool = candidates if position is None else mt.league_waivers(session, league_id, None, CANDIDATE_POOL)[2]
    by_value = sorted({c.player.player_id: c for c in [*pool, *candidates]}.values(), key=lambda c: -c.ros_value)
    demand_rank = {c.player.player_id: i + 1 for i, c in enumerate(by_value)}
    rows = []
    for c in candidates:
        value = team.valuation.by_id.get(c.player.player_id)
        if value is None or value.position not in mt.MODELED:
            continue
        gain, drop = lineup_gain.best_add(roster, droppable, value, horizon, projector, team.settings, protected)
        rank = demand_rank[c.player.player_id]
        if kind == "faab":
            balance = me.get("faab_balance")
            budget = float(rules.get("faab_budget") or 100)
            advice = wa.faab_advice(gain, rank, value.position, balance if balance is not None else budget, budget,
                                    weeks_left, rivals, (insights or {}).get("winning_bids_by_week", []),
                                    horizon.current_week)
        elif kind == "priority":
            mine = me.get("waiver_priority")
            ahead = [r["name"] for r in rivals if mine and r.get("priority") and r["priority"] < mine
                     and value.position in r["needs"]]
            starts = lineup_gain.starter_weeks(
                [v for v in roster if not drop or v.player_id != drop.player_id] + [value], value.player_id,
                horizon.weeks[:LOOKAHEAD_WEEKS], projector, team.settings)
            advice = wa.priority_advice(gain, rank, value.position, mine, ahead,
                                        value.position == "RB" and value.team in injured_starter_teams, starts)
        else:
            advice = wa.free_agent_advice(gain)
        rows.append((gain, c, drop, advice))
    rows.sort(key=lambda r: -r[0])
    return {"league": league, "rankings": rankings, "league_type": kind, "weeks_left": weeks_left,
            "my_priority": me.get("waiver_priority"), "my_balance": me.get("faab_balance"),
            "faab_budget": rules.get("faab_budget"), "trade_end_date": rules.get("trade_end_date"),
            "rows": rows[:limit]}


def _capacity(settings) -> int:
    return sum(settings.starters.values()) + settings.bench


def _active_spots(spots: Sequence) -> int:
    return sum(1 for s in spots if s.slot not in mt.INACTIVE_SLOTS)


def _forced_drop(roster: Sequence, horizon, projector, settings, is_over: bool) -> tuple[list, object | None]:
    """(roster, dropped player): a roster over capacity drops the player it misses least."""
    if not is_over or not roster:
        return list(roster), None
    candidates = sorted(roster, key=lambda v: v.ros_ppg)[:DROP_CANDIDATES]
    best = max(candidates, key=lambda d: (lineup_gain.season_points([v for v in roster if v.player_id != d.player_id],
                                                                      horizon, projector, settings), -d.ros_ppg))
    return [v for v in roster if v.player_id != best.player_id], best


def _open_spot_add(roster: Sequence, free_agents: Sequence, horizon, projector, settings) -> tuple[float, object | None]:
    """(gain, player) of the best free agent for a roster spot a 2-for-1 opens up."""
    scored = [(lineup_gain.gain(roster, horizon, projector, settings, add=[fa]), fa) for fa in free_agents]
    best = max(scored, key=lambda s: s[0], default=(0.0, None))
    return best if best[0] > 0 else (0.0, None)


def offer_score(my_gain: float, their_gain: float) -> float:
    """Rank by your gain, less part of what the deal costs them (deals that hurt them get turned down)."""
    return my_gain + THEIR_LOSS_PENALTY * min(their_gain, 0.0)


def _free_agents(session: Session, league_id: int, team) -> list:
    try:
        _, _, candidates = mt.league_waivers(session, league_id, None, FREE_AGENT_POOL)
    except LookupError:
        return []
    values = (team.valuation.by_id.get(c.player.player_id) for c in candidates)
    return [v for v in values if v is not None and v.position in mt.MODELED]


def score_trades(session: Session, league_id: int, by_team: Sequence) -> list[tuple]:
    """[(team offers, [(offer, my_gain, their_gain)])], best first: candidate offers scored by both lineups.

    - Keeps offers that help you (my_gain > 0) without costing them more than TRADE_GIVE_LIMIT points
      per weighted week left, ranked by offer_score, at most OFFERS_PER_TEAM per team.
    - Roster spots count: in a 2-for-1 the side getting two players drops the one it misses least
      (offer.their_drop), and if your roster was full the spot you open is filled by the best free
      agent (offer.my_add); both are in the gains.
    """
    team = mt.build_my_team(session, league_id)
    horizon, projector = lineup_gain.context_for(team)
    # Their gain uses neutral playoff weights: we don't know their playoff odds the way we know yours.
    neutral = lineup_gain.horizon(horizon.current_week, max(horizon.weeks), playoff_weeks(mt.league_rules(team.league)),
                                  None)
    mine = lineup_gain.roster_values(team)
    capacity = _capacity(team.settings)
    my_roster_full = _roster_full(team)
    free_agents = None
    rosters = mt.rosters(session, team.league)
    floor = -TRADE_GIVE_LIMIT * sum(neutral.weights.values())
    my_base = lineup_gain.season_points(mine, horizon, projector, team.settings)
    out = []
    for team_offers in by_team:
        spots = rosters.get(team_offers.team.id, [])
        theirs = lineup_gain.roster_values(team, spots)
        their_base = lineup_gain.season_points(theirs, neutral, projector, team.settings)
        their_spots = _active_spots(spots)
        scored = []
        for offer in team_offers.offers:
            give_ids = {p.player_id for p in offer.give}
            my_after = [v for v in mine if v.player_id not in give_ids] + [offer.get]
            my_gain = lineup_gain.season_points(my_after, horizon, projector, team.settings) - my_base
            my_add = None
            if len(offer.give) > 1 and my_roster_full:
                if free_agents is None:
                    free_agents = _free_agents(session, league_id, team)
                bonus, my_add = _open_spot_add(my_after, free_agents, horizon, projector, team.settings)
                my_gain += bonus
            their_after = [v for v in theirs if v.player_id != offer.get.player_id] + list(offer.give)
            is_over = their_spots + len(offer.give) - 1 > capacity
            their_after, their_drop = _forced_drop(their_after, neutral, projector, team.settings, is_over)
            their_gain = lineup_gain.season_points(their_after, neutral, projector, team.settings) - their_base
            my_gain, their_gain = round(my_gain, 2), round(their_gain, 2)
            if my_gain > 0 and their_gain >= floor:
                scored.append((replace(offer, their_drop=their_drop, my_add=my_add), my_gain, their_gain))
        if scored:
            scored.sort(key=lambda s: -offer_score(s[1], s[2]))
            out.append((team_offers, _distinct(scored)[:mt.OFFERS_PER_TEAM]))
    return sorted(out, key=lambda t: -offer_score(t[1][0][1], t[1][0][2]))


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


def trade_impact(session: Session, league_id: int, give_ids: Sequence[int], get_ids: Sequence[int]) -> dict:
    """A calculator trade in your league: both starting lineups' gains, and the rosters it can draw on.

    give must be on your roster. The other side is the team that rosters every player in get (None
    when they are spread over teams or free agents; then only your gain is scored).
    """
    team = mt.build_my_team(session, league_id)
    horizon, projector = lineup_gain.context_for(team)
    by_id = team.valuation.by_id
    mine = lineup_gain.roster_values(team)
    my_ids = {s.player_id for s in team.spots}
    missing = [pid for pid in give_ids if pid not in my_ids]
    if missing:
        raise ValueError(f"Player id(s) {missing} aren't on your roster in this league.")
    rosters = mt.rosters(session, team.league)
    owner = next((tid for tid, spots in rosters.items() if tid != team.team.id
                  and set(get_ids) <= {s.player_id for s in spots}), None)
    give = [by_id[p] for p in give_ids if p in by_id]
    get = [by_id[p] for p in get_ids if p in by_id]
    my_gain = lineup_gain.gain(mine, horizon, projector, team.settings, add=get, drop=give)
    their_gain, their_team = None, None
    if owner is not None:
        neutral = lineup_gain.horizon(horizon.current_week, max(horizon.weeks),
                                      playoff_weeks(mt.league_rules(team.league)), None)
        spots = rosters[owner]
        theirs = lineup_gain.roster_values(team, spots)
        their_gain = lineup_gain.gain(theirs, neutral, projector, team.settings, add=give, drop=get)
        their_team = session.get(Team, owner)
    pool = my_ids | {s.player_id for s in rosters.get(owner, [])}
    return {"your_gain": my_gain, "their_gain": their_gain,
            "their_team": their_team.name if their_team else None, "pool_ids": pool}
