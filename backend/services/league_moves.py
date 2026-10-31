"""Roster moves in YOUR league, scored by lineup gain: waiver claims with a named drop and the right
advice for the league's waiver type (services/waiver_advice.py), and trade offers that must help
your lineup without gutting theirs.
"""
import logging
from collections.abc import Sequence
from dataclasses import dataclass, replace

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
TRADE_GIVE_LIMIT = 0.5            # their lineup may lose at most this x weeks left
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
            "roster_full": bool(droppable), "protected": frozenset(protected), "rows": rows[:limit]}


def _trade_context(session: Session, team) -> tuple:
    """(your horizon, a neutral horizon for the other team, projector, your roster values, rosters, floor)."""
    horizon, projector = lineup_gain.context_for(team)
    # Their gain uses neutral playoff weights: we don't know their playoff odds the way we know yours.
    neutral = lineup_gain.horizon(horizon.current_week, max(horizon.weeks), playoff_weeks(mt.league_rules(team.league)),
                                  None)
    return (horizon, neutral, projector, lineup_gain.roster_values(team), mt.rosters(session, team.league),
            -TRADE_GIVE_LIMIT * len(horizon.weeks))


def score_trades(session: Session, league_id: int, by_team: Sequence) -> list[tuple]:
    """[(team offers, [(offer, my_gain, their_gain)])]: offers that help you (my_gain > 0) without costing
    them more than TRADE_GIVE_LIMIT x weeks left, best for you first."""
    team = mt.build_my_team(session, league_id)
    horizon, neutral, projector, mine, rosters, floor = _trade_context(session, team)
    out = []
    for team_offers in by_team:
        theirs = lineup_gain.roster_values(team, rosters.get(team_offers.team.id, []))
        scored = []
        for offer in team_offers.offers:
            my_gain = lineup_gain.gain(mine, horizon, projector, team.settings, add=[offer.get], drop=list(offer.give))
            their_gain = lineup_gain.gain(theirs, neutral, projector, team.settings, add=list(offer.give),
                                          drop=[offer.get])
            if my_gain > 0 and their_gain >= floor:
                scored.append((offer, my_gain, their_gain))
        if scored:
            out.append((team_offers, sorted(scored, key=lambda s: -s[1])))
    return sorted(out, key=lambda t: -t[1][0][1])


@dataclass(frozen=True)
class TradeScore:
    """One specific trade on the real rosters. Gains are None when they can't be computed (a player you'd
    send isn't on your roster, or the players you'd get aren't on any team)."""
    your_lineup_gain: float | None
    their_lineup_gain: float | None
    acceptable_to_them: bool | None          # their lineup loses no more than TRADE_GIVE_LIMIT x weeks left
    partner: str | None                      # the other team's name (a manager-chosen label)
    my_ids: frozenset[int]
    partner_ids: frozenset[int]
    note: str | None = None


def score_trade(session: Session, league_id: int, give_ids: Sequence[int], get_ids: Sequence[int]) -> TradeScore:
    """Lineup gain for both sides of one trade; the partner is the team rostering the `get` players.

    Raises ValueError when a `get` player is already yours or the `get` players are on different teams."""
    team = mt.build_my_team(session, league_id)
    horizon, neutral, projector, mine, rosters, floor = _trade_context(session, team)
    owner = {s.player_id: team_id for team_id, spots in rosters.items() for s in spots}
    my_ids = frozenset(s.player_id for s in rosters.get(team.team.id, []))
    if any(owner.get(pid) == team.team.id for pid in get_ids):
        raise ValueError("A player you'd receive is already on your roster. Try: put him in `give` instead.")
    partners = {owner[pid] for pid in get_ids if pid in owner}
    if len(partners) > 1:
        raise ValueError("The players you'd receive are on different teams. Try: one trade per team.")
    partner_id = next(iter(partners), None)
    partner_ids = frozenset(s.player_id for s in rosters.get(partner_id, [])) if partner_id else frozenset()
    names = {t.id: t.name for t in session.scalars(select(Team).where(Team.league_id == team.league.id))}
    base = TradeScore(None, None, None, names.get(partner_id), my_ids, partner_ids)
    if partner_id is None or any(pid not in owner for pid in get_ids):
        return replace(base, note="the players you'd receive aren't on a team in this league, so lineup gains "
                                  "aren't computed")
    if any(pid not in my_ids for pid in give_ids):
        return replace(base, note="a player you'd send isn't on your roster, so lineup gains aren't computed")
    values = team.valuation.by_id
    give = [values[pid] for pid in give_ids if pid in values]
    get = [values[pid] for pid in get_ids if pid in values]
    theirs = lineup_gain.roster_values(team, rosters.get(partner_id, []))
    yours = lineup_gain.gain(mine, horizon, projector, team.settings, add=get, drop=give)
    their = lineup_gain.gain(theirs, neutral, projector, team.settings, add=give, drop=get)
    return replace(base, your_lineup_gain=yours, their_lineup_gain=their, acceptable_to_them=their >= floor)
