"""Simulated seasons: the offseason fits judged on whole real seasons (run by sim_next_year_fit after the
rate fits; each season S is built like a future season from S-1, sim_eval.next_belief, and compared with S):

- `efficiency_scale` {position: s}: how much of the fitted per-rate regression (`efficiency_keep`) the belief
  going into a season applies, keep' = 1 - s x (1 - keep). Each rate's keep is honest for that rate alone, but
  a player's points are their product, and applying every one in full left the preseason belief too flat:
  real stars outscored it (points a game on our preseason value: slope 1.1-1.4). s is chosen per position from
  SCALE_GRID to minimize the squared error of the real points a game (GAMES_MIN games or more; veterans; QBs
  who were the believed starter) on the preseason value (sim_market.values, half PPR).
- `bench`: a logistic in a week-1 starting QB's preseason rank among the week-1 starters (by
  sim_market.position_ranks; sim_talent.public_ranks ranks the believed starters the same way) of the chance
  he loses the job healthy: a later week (before the last CHANGE_LAST_WEEKS) where another QB threw his
  team's most passing yards while he carried no injury tag that week or the two after, and he started
  fewer than half of the team's games from then on (sim_talent.bench_chance).
- `rookie_growth`: rookies (drafted that year, or first seen in the database that season) against veterans with
  the same first half: their opportunities a game after SPLIT_WEEK over before, as a chance of a growth step
  and its size (growth_fit; sim_talent dates the step around SPLIT_WEEK). Real rookies 2019-2025 gain about 6%
  more role than such veterans on average, the small-role ones about 15%; efficiency (points per opportunity)
  does not grow, so the step is on the role only.
"""
import logging
from collections import defaultdict
from collections.abc import Collection, Iterable, Mapping

import numpy as np

from services import replay_pool, sim_metrics, sim_world
from services.sim_talent import CHANGE_LAST_WEEKS

log = logging.getLogger(__name__)

SCORING = "half"
SKILL = ("QB", "RB", "WR", "TE")
SCALE_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
GAMES_MIN = 8
STARTER = 0.5                    # a QB with this starts weight is his team's believed starter
LOGIT_STEPS = 50
SPLIT_WEEK = 9                   # rookie growth: weeks 1-9 against 10 on
HALF_GAMES = 4
GROWTH_MIN_ROLE = 0.5            # opportunities a game in the first half (a rookie who barely played counts too)
GROWTH_QUANTILES = (25, 50, 75, 90)
GROWTH_GRID = (np.arange(0.05, 0.951, 0.05), np.arange(0.025, 1.0, 0.025))
GROWTH_DRAWS = 20000


def _belief(session, real, offseason):
    from services import sim_eval

    belief, _, rookies = sim_eval.next_belief(session, real, offseason)
    return belief, rookies


def errors(session, reals: Iterable, offseason: Mapping) -> dict[int, dict[str, tuple[float, int]]]:
    """{season: {position: (sum of squared errors, count)}} of the real points a game on the preseason value
    under the model in use (see the module docstring)."""
    from services import sim_market

    out = {}
    for real in reals:
        belief, rookies = _belief(session, real, offseason)
        worth = sim_market.values(belief, sim_world.scoring_of(real))
        games: dict[int, list[float]] = defaultdict(list)
        for (pid, _), points in real.actual.items():
            games[pid].append(points)
        cells: dict[str, list[float]] = defaultdict(list)
        for pid, value in worth.items():
            p = belief.players[pid]
            if (p.position in SKILL and len(games.get(pid, ())) >= GAMES_MIN and pid not in rookies
                    and (p.position != "QB" or p.starts >= STARTER)):
                cells[p.position].append((float(np.mean(games[pid])) - value) ** 2)
        out[real.season] = {position: (float(sum(e)), len(e)) for position, e in cells.items()}
    return out


def best_scale(by_scale: Mapping[float, Mapping[int, Mapping[str, tuple[float, int]]]],
               seasons: Iterable[int]) -> dict[str, float]:
    """{position: the scale with the least squared error over `seasons`} from errors() per scale."""
    seasons = list(seasons)
    return {position: min(by_scale, key=lambda s: (sum(by_scale[s][y].get(position, (0.0, 0))[0] for y in seasons), s))
            for position in SKILL}


def lost_jobs(session, reals: Iterable, offseason: Mapping) -> list[tuple[int, int, bool]]:
    """[(season, preseason rank, lost the job healthy)] of every real week-1 starting QB (module docstring)."""
    from services import sim_market

    rows = []
    for real in reals:
        belief, _ = _belief(session, real, offseason)
        ranks = sim_market.position_ranks(belief)
        starter: dict[tuple[str | None, int], int] = {}
        for pid, weeks in sim_metrics.qb_starts(real).items():
            starter.update({(real.team_at(pid, week), week): pid for week in weeks})
        week1 = sorted(((team, pid) for (team, week), pid in starter.items() if week == 1), key=lambda tp: str(tp[0]))
        order = sorted((pid for _, pid in week1), key=lambda pid: (ranks.get(pid, len(ranks)), pid))
        among = {pid: rank for rank, pid in enumerate(order, start=1)}      # ranked among the starters
        for team, pid in week1:
            weeks = sorted(w for t, w in starter if t == team)
            rows.append((real.season, among[pid], _lost(real, starter, team, pid, weeks)))
    return rows


def _lost(real, starter: Mapping, team: str, pid: int, weeks: list[int]) -> bool:
    for week in weeks[1:]:
        if starter[(team, week)] == pid:
            continue
        if week > real.last_week - CHANGE_LAST_WEEKS or any((pid, w) in real.injuries for w in range(week, week + 3)):
            return False
        rest = [w for w in weeks if w >= week]
        return sum(1 for w in rest if starter[(team, w)] == pid) < len(rest) / 2
    return False


def logistic(rows: Iterable[tuple[int, int, bool]]) -> dict:
    """{"logit": [a, b], "n", "lost"}: P(lost) = 1 / (1 + exp(-(a + b x rank))), maximum likelihood (Newton)."""
    rows = list(rows)
    x = np.array([[1.0, rank] for _, rank, _ in rows])
    y = np.array([float(lost) for _, _, lost in rows])
    beta = np.zeros(2)
    for _ in range(LOGIT_STEPS):
        p = 1.0 / (1.0 + np.exp(-x @ beta))
        hessian = x.T @ (x * (p * (1 - p))[:, None]) + 1e-9 * np.eye(2)
        beta = beta + np.linalg.solve(hessian, x.T @ (y - p))
    return {"logit": [round(float(beta[0]), 4), round(float(beta[1]), 4)], "n": len(rows), "lost": int(y.sum())}


def halves(data) -> dict[int, tuple[tuple[int, float, float], tuple[int, float, float]]]:
    """{player: ((games, opportunities, points) through SPLIT_WEEK, the same after)} of his played games."""
    out: dict[int, list[list[float]]] = defaultdict(lambda: [[0, 0.0, 0.0], [0, 0.0, 0.0]])
    for (pid, week), line in data.box.items():
        half = out[pid][week > SPLIT_WEEK]
        half[0] += 1
        half[1] += line.targets + line.carries
        half[2] += data.actual.get((pid, week), 0.0)
    return {pid: (tuple(a), tuple(b)) for pid, (a, b) in out.items()}


def growth_rows(data, rookies: Collection[int]) -> list[tuple[str, bool, float, float]]:
    """[(position, rookie, opportunities a game before SPLIT_WEEK, after)] of the backs and receivers with
    HALF_GAMES games in each half and GROWTH_MIN_ROLE opportunities a game in the first."""
    rows = []
    for pid, (a, b) in halves(data).items():
        position = data.players[pid].position if pid in data.players else None
        if position in ("RB", "WR", "TE") and a[0] >= HALF_GAMES and b[0] >= HALF_GAMES:
            before, after = a[1] / a[0], b[1] / b[0]
            if before >= GROWTH_MIN_ROLE and after > 0:
                rows.append((position, pid in rookies, before, after))
    return rows


def growth_fit(rows: list[tuple[str, bool, float, float]]) -> dict:
    """{"chance", "gain", "split_week", "n"}: a rookie's role after SPLIT_WEEK x `gain` with `chance` (else as a
    veteran's), the (chance, log gain) on GROWTH_GRID whose mixture of the veterans' residuals (log second-half
    over first-half role, net of a line in the first-half role and position) best matches the rookies'
    residual quantiles GROWTH_QUANTILES (the lowest decile is left out: a rookie who loses his role is not
    growth). Quantiles, not the likelihood: the rookies' wider spread both ways would otherwise read as noise."""
    x = np.log([before for *_, before, _ in rows])
    y = np.log([after for *_, after in rows]) - x
    design = np.column_stack([np.array([r[0] == p for r in rows], float) for p in ("RB", "WR", "TE")] + [x - x.mean()])
    veteran = np.array([not r[1] for r in rows])
    beta, *_ = np.linalg.lstsq(design[veteran], y[veteran], rcond=None)
    residual = y - design @ beta
    target = np.percentile(residual[~veteran], GROWTH_QUANTILES)
    dice = np.random.default_rng(0)
    draws = dice.choice(residual[veteran], GROWTH_DRAWS)
    coin = dice.random(GROWTH_DRAWS)

    def loss(chance: float, gain: float) -> float:
        return float(((np.percentile(draws + np.where(coin < chance, gain, 0.0), GROWTH_QUANTILES) - target) ** 2).sum())

    chance, gain = min(((c, g) for c in GROWTH_GRID[0] for g in GROWTH_GRID[1]), key=lambda cg: loss(*cg))
    return {"chance": round(float(chance), 3), "gain": round(float(np.exp(gain)), 3), "split_week": SPLIT_WEEK,
            "n": int((~veteran).sum()), "mean_residual": round(float(residual[~veteran].mean()), 4)}


def load(session, seasons: Iterable[int]) -> list:
    return [replay_pool.load_season_data(session, season, SCORING) for season in seasons]
