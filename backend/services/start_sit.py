"""Start/sit between 2-4 players: who to start this week, how sure, and why.

- Projections, ranges, chance to play and matchups come from this week's blended rankings
  (services/weekly_rankings.py), in the user's league scoring when a Yahoo league applies:
  the named league, else the only imported league, else the one whose roster holds most of
  the players. Without a league: the default settings (PPR, 12 teams).
- A player whose game has kicked off is locked: he can't be started or benched now, so he is
  never the pick (services/game_locks.py).
- Confidence comes from the projection gap between the pick and the runner-up, with the same
  cut-offs as the lineup advice (my_team.TOSS_UP_POINTS / LEAN_POINTS: 2021-26 start/sit accuracy
  was 51% under 1 point apart and 58% at 2-3).
- p_outscores = the share of SIMULATIONS correlated simulated weeks (services/matchup_sim.py,
  fixed seed) in which the pick scores more than the runner-up (ties count half).
"""
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np
from sqlalchemy.orm import Session

from services import game_locks, matchup_sim
from services import my_team as mt
from services.insights import RECENT_GAMES, compute_signals
from services.league import LeagueSettings
from services.players import resolve_player
from services.valuation import Valuation, build_valuation
from services.weekly_rankings import WeeklyPlayer, build_weekly_rankings

SIMULATIONS = 10_000
SEED = 7
USAGE_TREND_MIN = 1.0              # expected points/game change worth mentioning
SLOT_ALIASES = {"W/R/T": "FLEX", "W/R": "FLEX", "W/T": "FLEX", "RB/WR/TE": "FLEX", "OP": "SUPERFLEX",
                "Q/W/R/T": "SUPERFLEX", "DEF": "DST", "D/ST": "DST"}


class StartSitError(ValueError):
    """A request the tool can't answer as asked; the message is safe to show a model."""


@dataclass(frozen=True)
class Context:
    """Everything a decision needs for one week, from a league or from default settings."""
    settings: LeagueSettings
    week: int
    weekly: Mapping[int, WeeklyPlayer]
    valuation: Valuation
    kickoff: Mapping[int, datetime]
    locked: frozenset[int]
    alerts: tuple[str, ...]
    league_name: str | None


def confidence_for(gap: float) -> str:
    """'toss_up' (under 1.5 points apart), 'lean' (1.5-3) or 'clear'."""
    return "toss_up" if gap < mt.TOSS_UP_POINTS else "lean" if gap < mt.LEAN_POINTS else "clear"


def _pick_league(session: Session, league: str | None, player_ids: Sequence[int]):
    """(League, Team) to judge in, or None for default settings."""
    if league:
        try:
            return mt.find_league(session, league)
        except mt.LeagueNotFound as exc:
            raise StartSitError(str(exc)) from exc
    leagues = mt.my_leagues(session)
    if len(leagues) == 1:
        return leagues[0]

    def held(pair) -> int:
        spots = mt.rosters(session, pair[0]).get(pair[1].id, [])
        return len({s.player_id for s in spots} & set(player_ids))

    ranked = sorted(((held(pair), pair) for pair in leagues), key=lambda item: -item[0])
    if ranked and ranked[0][0] > 0 and (len(ranked) == 1 or ranked[0][0] > ranked[1][0]):
        return ranked[0][1]
    return None


def build_context(session: Session, league: str | None, player_ids: Sequence[int]) -> Context:
    pair = _pick_league(session, league, player_ids)
    if pair is not None:
        team = mt.build_my_team(session, pair[0].id)
        return Context(team.settings, team.rankings.week, team.weekly(), team.valuation, dict(team.kickoff),
                       frozenset(team.locked), tuple(team.alerts), team.league.name)
    settings = LeagueSettings()
    rankings = build_weekly_rankings(session, settings)
    valuation = build_valuation(session, settings)
    weekly = {p.player_id: p for p in rankings.players if p.player_id in player_ids}
    kickoff = game_locks.kickoffs(list(weekly.values()), valuation.schedule, rankings.week)
    locked = game_locks.locked_ids(list(weekly.values()), kickoff)
    return Context(settings, rankings.week, weekly, valuation, kickoff, frozenset(locked), (), None)


def outscore_probabilities(players: Sequence[WeeklyPlayer], seed: int = SEED,
                           n: int = SIMULATIONS) -> dict[tuple[int, int], float]:
    """{(a, b): chance player a outscores player b} for every ordered pair (ties count half)."""
    scores = matchup_sim.simulate_scores(players, np.random.default_rng(seed), n)
    out = {}
    for i, a in enumerate(players):
        for j, b in enumerate(players):
            if i != j:
                out[(a.player_id, b.player_id)] = float(np.mean(scores[:, i] > scores[:, j])
                                                        + 0.5 * np.mean(scores[:, i] == scores[:, j]))
    return out


def _slot_filter(slot: str | None, players: Sequence[WeeklyPlayer]) -> tuple[str | None, list[WeeklyPlayer]]:
    if not slot:
        return None, list(players)
    name = SLOT_ALIASES.get(slot.strip().upper(), slot.strip().upper())
    return name, [p for p in players if mt._eligible(name, p.position)]


def usage_trend(valuation: Valuation, player_id: int) -> str | None:
    """'usage rising: +2.1 expected pts/game over the last 3' when the change is notable."""
    season = next((s for s in valuation.seasons if s.player_id == player_id), None)
    signals = compute_signals(season) if season is not None else None
    if signals is None or signals.usage_trend is None or abs(signals.usage_trend) < USAGE_TREND_MIN:
        return None
    direction = "rising" if signals.usage_trend > 0 else "falling"
    return f"usage {direction}: {signals.usage_trend:+.1f} expected pts/game over the last {RECENT_GAMES}"


def _verdict(pick: WeeklyPlayer, runner: WeeklyPlayer | None, confidence: str, gap: float,
             p_outscores: float | None, ctx: Context) -> str:
    if runner is None:
        return f"Start {pick.name}: no other option can start (see notes)."
    odds = f"; he outscores {runner.name} in {round(p_outscores * 100)}% of simulations" if p_outscores is not None else ""
    if confidence != "toss_up":
        return f"Start {pick.name} over {runner.name} ({confidence.replace('_', '-')}: {gap:.1f} projected points apart{odds})."
    tiebreak = ""
    late = ctx.kickoff.get(runner.player_id), ctx.kickoff.get(pick.player_id)
    if late[0] and late[1] and late[0] > late[1]:
        tiebreak = f" {runner.name} plays later, so starting him keeps a late swap open."
    elif (runner.ceiling or 0) > (pick.ceiling or 0):
        tiebreak = f" {runner.name} has the higher ceiling if you need upside."
    return f"Toss-up: {pick.name} by {gap:.1f} projected points{odds}, close to a coin flip.{tiebreak}"


def decide(session: Session, players: Sequence[str], league: str | None = None, slot: str | None = None) -> dict:
    """The decision as plain data; tools/start_sit_tool.py shapes it for the model."""
    refs = list({ref.id: ref for ref in (resolve_player(session, name) for name in players)}.values())
    if len(refs) < 2:
        raise StartSitError("Name at least two different players to compare.")
    ctx = build_context(session, league, [r.id for r in refs])
    found = [ctx.weekly[r.id] for r in refs if r.id in ctx.weekly]
    missing = [r.name for r in refs if r.id not in ctx.weekly]
    slot_name, eligible = _slot_filter(slot, found)
    open_players = sorted((p for p in eligible if p.player_id not in ctx.locked), key=lambda p: -p.projection)
    notes = [f"{name}: no projection this week (bye, ruled out or not covered)." for name in missing]
    notes += [f"{p.name}'s game has kicked off: he can't be moved now." for p in found if p.player_id in ctx.locked]
    if slot_name:
        eligible_ids = {p.player_id for p in eligible}
        notes += [f"{p.name} can't play {slot_name}." for p in found if p.player_id not in eligible_ids]
    decision: dict = {"start": None, "confidence": None, "gap": None, "p_outscores": None,
                      "verdict": "No player here can be started now: their games have kicked off or they can't "
                                 "fill the slot."}
    if open_players:
        pick = open_players[0]
        runner = open_players[1] if len(open_players) > 1 else None
        gap = round(pick.projection - runner.projection, 1) if runner else None
        confidence = confidence_for(gap) if runner else "clear"
        odds = outscore_probabilities([pick, runner]) if runner else {}
        p_out = round(odds[(pick.player_id, runner.player_id)], 2) if runner else None
        decision = {"start": pick, "runner_up": runner, "confidence": confidence, "gap": gap, "p_outscores": p_out,
                    "verdict": _verdict(pick, runner, confidence, gap or 0.0, p_out, ctx)}
    names = {r.name for r in refs}
    return {"settings": ctx.settings, "week": ctx.week, "league": ctx.league_name, "players": found,
            "kickoff": ctx.kickoff, "locked": ctx.locked, "decision": decision, "notes": notes,
            "trends": {p.player_id: usage_trend(ctx.valuation, p.player_id) for p in found},
            "alerts": [a for a in ctx.alerts if any(a.startswith(n + " (") for n in names)]}
