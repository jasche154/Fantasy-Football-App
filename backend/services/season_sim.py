"""Power rankings and the season simulation behind playoff odds (used by services/league_insights.py).

Power rankings
- Strength: each team's best lineup of QB/RB/WR/TE by rest-of-season points per game
  (kickers and defenses aren't projected, the same for every team).
- Weekly projection (best ball): for every remaining week, each team's best lineup from its
  whole roster (not IR), each player projected against that week's opponent:
      rest-of-season points per game x (opponent defense factor)^ROS_SCHEDULE_WEIGHT
  A player on bye is out that week. For each player a team has on bye, it also gets the
  week's best free agent at his position (the best unrostered player in the league, not on
  bye) as a rough waiver pickup, so a bye week costs only the gap to the waiver wire.
- Playoff odds: the rest of the regular season is simulated SIMULATIONS times from Yahoo's
  real remaining schedule. In each simulated season:
    * every team gets one strength offset, Normal(0, SEASON_SD): rest-of-season estimates miss
      by about this much, and the miss lasts all season (it doesn't average out week to week);
    * every starter can get hurt: each week he misses with his position's hazard (QB 8.8%,
      WR 5.4%, RB/TE 4.3%), and once out stays out the next week 60% of the time; a player
      already hurt starts out for his expected games missed (services/injury_recovery.py). His
      spot goes to the best free agent;
    * each game a team scores Normal(that week's projection + offset - injury losses, WEEKLY_SD).
  Wins and points for are added to the current standings, and the top playoff_teams (wins,
  then points for) make it. Projections use healthy rates, since injuries are simulated.
"""
import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from types import SimpleNamespace

import numpy as np

from services import my_team as mt
from services.valuation import ROS_SCHEDULE_WEIGHT

SIMULATIONS = 10_000
WEEKLY_SD = 21.0                 # one team's weekly total around its projection (skill positions, 2021-25)
SEASON_SD = 9.0                  # how far a team's rest-of-season strength estimate misses, all season


def lineup_strength(valuation, settings, spots: Sequence[mt.RosterSpot]) -> float:
    """Rest-of-season points per game of the team's best QB/RB/WR/TE lineup."""
    players = [SimpleNamespace(player_id=v.player_id, position=v.position, projection=v.ros_ppg)
               for s in spots if s.slot not in ("IR", "IR+", "NA")
               and (v := valuation.by_id.get(s.player_id)) is not None and v.position in mt.MODELED]
    best = mt.best_lineup(players, settings)
    by_id = {p.player_id: p.projection for p in players}
    return round(sum(by_id[pid] for ids in best.values() for pid in ids), 1)


def weekly_strengths(rosters: Mapping[str, Sequence], free_agents: Sequence, weeks: Sequence[int], settings,
                     project: Callable[[object, int], float | None]) -> dict[str, dict[int, float]]:
    """{team: {week: best-ball lineup projection}} (see the module docstring)."""
    lineups = weekly_lineups(rosters, free_agents, weeks, settings, project)
    return {team: {week: round(sum(points for _, _, points, _ in starters), 1) for week, starters in by_week.items()}
            for team, by_week in lineups.items()}


def weekly_lineups(rosters: Mapping[str, Sequence], free_agents: Sequence, weeks: Sequence[int], settings,
                   project: Callable[[object, int], float | None]) -> dict[str, dict[int, list[tuple]]]:
    """{team: {week: [(player id, position, points, waiver replacement points)]}} for each starter.

    rosters hold each team's player values (IR excluded); project(value, week) is that week's
    projection, or None on a bye. Waiver fill-ins (for byes) have negative ids.
    """
    waiver: dict[tuple[str, int], float] = {}
    for value in free_agents:
        for week in weeks:
            points = project(value, week)
            if points is not None and points > waiver.get((value.position, week), 0.0):
                waiver[(value.position, week)] = points
    out: dict[str, dict[int, list[tuple]]] = {}
    for team, values in rosters.items():
        out[team] = {}
        for week in weeks:
            pool, byes = [], Counter()
            for value in values:
                points = project(value, week)
                if points is None:
                    byes[value.position] += 1
                else:
                    pool.append(SimpleNamespace(player_id=value.player_id, position=value.position, projection=points))
            for position, count in byes.items():          # one rough pickup per player on bye
                pool += [SimpleNamespace(player_id=-(len(pool) + i + 1), position=position,
                                         projection=waiver.get((position, week), 0.0)) for i in range(count)]
            best = mt.best_lineup(pool, settings)
            by_id = {p.player_id: p for p in pool}
            out[team][week] = [(pid, by_id[pid].position, by_id[pid].projection,
                                waiver.get((by_id[pid].position, week), 0.0)) for ids in best.values() for pid in ids]
    return out


def _start_out(valuation) -> dict[int, int]:
    """{player id: weeks certainly out}: his expected games missed (services/injury_recovery.py), rounded."""
    out = {v.player_id: math.floor(v.expected_missed + 0.5) for v in valuation.values}     # half-up, not to even
    return {pid: weeks for pid, weeks in out.items() if weeks > 0}


def _active_values(valuation, spots: Sequence[mt.RosterSpot]) -> list:
    return [v for s in spots if s.slot not in ("IR", "IR+", "NA")
            and (v := valuation.by_id.get(s.player_id)) is not None and v.position in mt.MODELED and v.team]


def _free_agents(valuation, rosters: Mapping[int, Sequence[mt.RosterSpot]]) -> list:
    rostered = {s.player_id for spots in rosters.values() for s in spots}
    return [v for v in valuation.values if v.position in mt.MODELED and v.team and v.player_id not in rostered]


def _projector(valuation) -> Callable[[object, int], float | None]:
    """Healthy rate x matchup (the season simulation adds injuries itself)."""
    schedule, defense = valuation.schedule, valuation.defense

    def project(value, week: int) -> float | None:
        opponent = schedule.opponent(value.team, week)
        if opponent is None:
            return None                                   # bye
        share = getattr(value, "availability", 1.0) or 1.0
        rate = value.ros_ppg / share if share > 0.05 else value.ros_ppg
        return rate * defense.factor(opponent, value.position) ** ROS_SCHEDULE_WEIGHT
    return project


def injury_losses(lineups: Mapping[int, list[tuple]], start_out: Mapping[int, int], rng: np.random.Generator,
                  sims: int) -> dict[int, np.ndarray]:
    """{week: (sims,) points lost to starter injuries} for one team (see the module docstring).

    start_out: {player id: weeks he's certainly out (IR) or 1 (Out now)}.
    """
    from services.lineup_gain import HAZARD, OUT_PERSIST

    weeks = sorted(lineups)
    players = {pid: pos for week in weeks for pid, pos, _, _ in lineups[week] if pid > 0}
    out = {pid: np.full(sims, start_out.get(pid, 0) > 0) for pid in players}
    losses = {}
    for index, week in enumerate(weeks):
        if index:
            for pid, pos in players.items():
                forced = start_out.get(pid, 0) > index
                stays = rng.random(sims) < OUT_PERSIST
                hurt = rng.random(sims) < HAZARD.get(pos, 0.05)
                out[pid] = forced | np.where(out[pid], stays, hurt)
        loss = np.zeros(sims)
        for pid, _, points, replacement in lineups[week]:
            if pid > 0:
                loss += out[pid] * max(points - replacement, 0.0)
        losses[week] = loss
    return losses


def simulate_season(teams: Sequence[str], strength: Mapping[str, Mapping[int, float]], wins: Mapping[str, float],
                    points_for: Mapping[str, float], games: Sequence[tuple[int, str, str]], playoff_teams: int,
                    sims: int = SIMULATIONS, seed: int = 11, season_sd: float = SEASON_SD,
                    losses: Mapping[str, Mapping[int, np.ndarray]] | None = None) -> dict[str, dict]:
    """{team: {playoff_odds, expected_wins, top_seed_odds}} over the remaining (week, home, away) games:
    each team scores around its projection for that week, plus its season offset, minus injury losses."""
    rng = np.random.default_rng(seed)
    index = {t: i for i, t in enumerate(teams)}
    total_wins = np.tile(np.array([wins.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    total_points = np.tile(np.array([points_for.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    offsets = rng.normal(0.0, season_sd, (sims, len(teams))) if season_sd else np.zeros((sims, len(teams)))
    losses = losses or {}

    def mean(team: str, week: int) -> np.ndarray:
        lost = losses.get(team, {}).get(week)
        return strength.get(team, {}).get(week, 0.0) + offsets[:, index[team]] - (lost if lost is not None else 0.0)

    for week, home, away in games:
        if home not in index or away not in index:
            continue
        a, b = index[home], index[away]
        score_a = rng.normal(mean(home, week), WEEKLY_SD)
        score_b = rng.normal(mean(away, week), WEEKLY_SD)
        total_wins[:, a] += score_a > score_b
        total_wins[:, b] += score_b > score_a
        total_points[:, a] += score_a
        total_points[:, b] += score_b
    # Seeds: wins first, points for breaks ties (scaled far below one win).
    order = np.argsort(-(total_wins * 1e6 + total_points), axis=1)
    seeds = np.empty_like(order)
    seeds[np.arange(sims)[:, None], order] = np.arange(len(teams))
    cutoff = min(max(playoff_teams, 1), len(teams))
    return {t: {"playoff_odds": round(float((seeds[:, i] < cutoff).mean()), 3),
                "top_seed_odds": round(float((seeds[:, i] == 0).mean()), 3),
                "expected_wins": round(float(total_wins[:, i].mean()), 1)}
            for t, i in index.items()}
