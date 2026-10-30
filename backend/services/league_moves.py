"""Roster moves in YOUR league, scored by lineup gain: waiver claims with a named drop and the right
advice for the league's waiver type (services/waiver_advice.py), and trade offers that must help
your lineup without gutting theirs.
"""
from collections.abc import Sequence

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import get_settings
from database.models import Team
from services import league_insights, lineup_gain
from services import my_team as mt
from services import waiver_advice as wa

CANDIDATE_POOL = 30               # waiver candidates scored by lineup gain (from the value-ranked list)
TRADE_GIVE_LIMIT = 0.5            # their lineup may lose at most this x weeks left
LOOKAHEAD_WEEKS = 4
HTTP_TIMEOUT_SECONDS = 15.0


def _insights(session: Session, league_id: int) -> dict | None:
    """Standings, balances, priorities and bid history (cached an hour); None if Yahoo is unreachable."""
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            return league_insights.build(session, get_settings(), http, league_id)
    except Exception:                 # Yahoo down or not connected: advice falls back to estimates
        return None


def _roster_full(team) -> bool:
    capacity = sum(team.settings.starters.values()) + team.settings.bench
    return sum(1 for s in team.spots if s.slot not in ("IR", "IR+", "NA")) >= capacity


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
    by_value = sorted(candidates, key=lambda c: -c.ros_value)
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


def score_trades(session: Session, league_id: int, by_team: Sequence) -> list[tuple]:
    """[(team offers, [(offer, my_gain, their_gain)])]: offers that help you (my_gain > 0) without costing
    them more than TRADE_GIVE_LIMIT x weeks left, best for you first."""
    team = mt.build_my_team(session, league_id)
    horizon, projector = lineup_gain.context_for(team)
    mine = lineup_gain.roster_values(team)
    rosters = mt.rosters(session, team.league)
    floor = -TRADE_GIVE_LIMIT * len(horizon.weeks)
    out = []
    for team_offers in by_team:
        theirs = lineup_gain.roster_values(team, rosters.get(team_offers.team.id, []))
        scored = []
        for offer in team_offers.offers:
            my_gain = lineup_gain.gain(mine, horizon, projector, team.settings, add=[offer.get], drop=list(offer.give))
            their_gain = lineup_gain.gain(theirs, horizon, projector, team.settings, add=list(offer.give),
                                          drop=[offer.get])
            if my_gain > 0 and their_gain >= floor:
                scored.append((offer, my_gain, their_gain))
        if scored:
            out.append((team_offers, sorted(scored, key=lambda s: -s[1])))
    return sorted(out, key=lambda t: -t[1][0][1])
