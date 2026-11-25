"""Simulated seasons: realism numbers for whole NFL games and for the expert ranks (used by sim_eval).

A real season and a simulated one are read alike: the pregame line is the schedule's spread and total (a real
season's Vegas closing line; a simulated week's own line, sim_lines, stored there by sim_season), the result is
`SeasonData.scores`.

- games(data, weeks): correlation of the line with the result (margin, total), the spread of pregame margins,
  the residual spread, the share of games won by 21+ / 28+ / 35+, the 21+ share in the most lopsided and the
  closest fifth of games by the line, the home win rate, the tie rate, and team points (mean, spread, the share
  that are 0 and the share that are rare in real games: RARE_TOTALS).
- experts(data, weeks): expert-rank churn, the mean change from one week to the next of a healthy player's
  position rank among the top CHURN_TOP at his position (no injury tag either week), and the agreement of the
  expert ranks with the week's projections (Spearman over the CHURN_TOP by expert rank who have one), by position.
"""
from collections.abc import Iterable

import numpy as np

from services.replacement_eval import spearman
from services.replay_pool import SeasonData

BLOWOUTS = (21, 28, 35)
RARE_TOTALS = (1, 2, 4, 5, 8)    # team scores of under 0.25% of real games 2018-2025 (12 of 4,194)
CHURN_TOP = {"QB": 16, "RB": 24, "WR": 36, "TE": 12}
FIFTH = 0.2


def _corr(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.corrcoef(a, b)[0, 1]) if len(a) > 2 and a.std() > 0 and b.std() > 0 else float("nan")


def games(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    weeks = set(weeks)
    rows = []
    for g in data.schedule.games:
        hs, as_ = data.scores.get((g.home_team, g.week)), data.scores.get((g.away_team, g.week))
        if g.week in weeks and hs is not None and as_ is not None and g.spread_line is not None and g.total_line is not None:
            rows.append((g.spread_line, g.total_line, hs - as_, hs + as_, hs, as_))
    if len(rows) < 5:
        return {}
    line_m, line_t, margin, total, home, away = np.array(rows, dtype=float).T
    big = np.abs(line_m) >= np.quantile(np.abs(line_m), 1 - FIFTH)
    close = np.abs(line_m) <= np.quantile(np.abs(line_m), FIFTH)
    points = np.concatenate([home, away])
    out = {"line r margin": _corr(line_m, margin), "line r total": _corr(line_t, total),
           "line margin sd": float(line_m.std()), "result - line sd": float((margin - line_m).std()),
           "line home edge": float(line_m.mean()), "home edge": float(margin.mean())}
    out.update({f"{b}+ margin %": 100 * float((np.abs(margin) >= b).mean()) for b in BLOWOUTS})
    out.update({"21+ in lopsided 5th %": 100 * float((np.abs(margin[big]) >= 21).mean()),
                "21+ in closest 5th %": 100 * float((np.abs(margin[close]) >= 21).mean()),
                "home win %": 100 * float((margin > 0).mean() + 0.5 * (margin == 0).mean()),
                "tie %": 100 * float((margin == 0).mean()), "team score mean": float(points.mean()),
                "team score sd": float(points.std()), "team score 0 %": 100 * float((points == 0).mean()),
                "rare score %": 100 * float(np.isin(points, RARE_TOTALS).mean())})
    return out


def _ranked(data: SeasonData, week: int, position: str) -> dict[int, int]:
    return {pid: rank for (pid, w), rank in data.expert_ranks.items()
            if w == week and pid in data.players and data.players[pid].position == position}


def experts(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    weeks = sorted(set(weeks))
    tagged = {key for key, injury in data.injuries.items() if injury.tag}
    out = {}
    for position, top in CHURN_TOP.items():
        moves, agree = [], []
        for week in weeks:
            now = _ranked(data, week, position)
            before = _ranked(data, week - 1, position) if week - 1 in weeks else {}
            moves += [abs(now[pid] - rank) for pid, rank in before.items()
                      if rank <= top and pid in now and (pid, week - 1) not in tagged and (pid, week) not in tagged]
            listed = [(rank, data.projected[(pid, week)]) for pid, rank in now.items()
                      if rank <= top and (pid, week) in data.projected]
            if len(listed) >= 5:
                agree.append(spearman([-r for r, _ in listed], [p for _, p in listed]))
        if moves:
            out[f"expert churn {position}"] = float(np.mean(moves))
        if agree:
            out[f"expert vs proj r {position}"] = float(np.nanmean(agree))
    return out
