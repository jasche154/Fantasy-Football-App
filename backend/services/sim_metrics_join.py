"""Simulated seasons: how the public tables move from week to week, read alike on a real season and a simulated
one (sim_eval). In a mid-season simulation `week` is the first simulated one, so week - 1 is the last real week:
a real season measured the same way shows how much a normal week moves.

- join(data, week): from week - 1 to `week`, for each position's healthy top players by the expert ranks of
  week - 1 (no tag either week): the mean projection's ratio (`week` over week - 1) and the experts' mean rank
  change.
- churn(data, weeks): the week-to-week change of a healthy top player's projection (points, absolute; median and
  90th percentile), the expert-rank churn's 90th percentile beside sim_metrics_games' mean.
- line_follow(data, weeks): how far a weekly projection follows the team's implied total (elasticity, by position).
- absences(data, weeks): no-shows a week (a QB, RB, WR or TE projected PLAYING_PROJECTION+ whose team played, no box line
  and no tag at all), zero projections a week (a player projected PLAYING_PROJECTION+ the week before, no tag,
  projected ZERO_PROJECTION or less, and he played), and the share of team scores of 6 or fewer.
"""
from collections.abc import Iterable

import numpy as np

from services.replay_pool import SeasonData

TOP = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}
PLAYING_PROJECTION = 8.0         # a projection this high says he is expected to play (item 29's measure)
ZERO_PROJECTION = 0.5            # a projection this low says he is not expected to play
LOW_SCORE = 6                    # team points: a score this low is rare (real ~4% of team-games)
LATE_WEEK = 15                   # ... also measured from this week on (item 30: low scores late in the season)


def _top(data: SeasonData, week: int, position: str) -> list[int]:
    ranked = sorted((rank, pid) for (pid, w), rank in data.expert_ranks.items()
                    if w == week and pid in data.players and data.players[pid].position == position)
    return [pid for rank, pid in ranked if rank <= TOP[position]]


def _healthy(data: SeasonData, pid: int, *weeks: int) -> bool:
    return not any((pid, w) in data.injuries for w in weeks)


def join(data: SeasonData, week: int) -> dict[str, float]:
    out = {}
    for position in TOP:
        before = {pid: rank for rank, pid in enumerate(_top(data, week - 1, position), start=1)
                  if _healthy(data, pid, week - 1, week)}
        now = {pid: rank for (pid, w), rank in data.expert_ranks.items() if w == week and pid in before}
        pairs = [(data.projected[(pid, week - 1)], data.projected[(pid, week)]) for pid in before
                 if (pid, week - 1) in data.projected and (pid, week) in data.projected]
        if pairs:
            old, new = np.array(pairs).T
            out[f"join proj ratio {position}"] = float(new.mean() / old.mean()) if old.mean() else float("nan")
        if now:
            out[f"join expert churn {position}"] = float(np.mean([abs(now[pid] - before[pid]) for pid in now]))
    return out


def churn(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    weeks = sorted(set(weeks))
    out = {}
    for position in TOP:
        moves, ranks = [], []
        for week in weeks:
            if week - 1 not in weeks:
                continue
            for rank, pid in enumerate(_top(data, week - 1, position), start=1):
                if not _healthy(data, pid, week - 1, week):
                    continue
                if (pid, week - 1) in data.projected and (pid, week) in data.projected:
                    moves.append(abs(data.projected[(pid, week)] - data.projected[(pid, week - 1)]))
                if (pid, week) in data.expert_ranks:
                    ranks.append(abs(data.expert_ranks[(pid, week)] - rank))
        if moves:
            out[f"proj churn {position} median"] = float(np.median(moves))
            out[f"proj churn {position} p90"] = float(np.percentile(moves, 90))
        if ranks:
            out[f"expert churn {position} p90"] = float(np.percentile(ranks, 90))
    return out


def line_follow(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    """How far a player's weekly projection follows his team's implied total (from the line), within player-season:
    the slope of log projection on log implied total, healthy players projected PLAYING_PROJECTION+ (real ESPN /
    Sleeper 2022-2024: QB 0.55, RB 0.28, WR 0.22, TE 0.12)."""
    weeks, totals = set(weeks), {}
    for g in data.schedule.games:
        if g.week in weeks and g.spread_line is not None and g.total_line is not None and g.total_line > abs(g.spread_line):
            totals[(g.home_team, g.week)] = (g.total_line + g.spread_line) / 2
            totals[(g.away_team, g.week)] = (g.total_line - g.spread_line) / 2
    rows: dict[str, dict[int, list]] = {position: {} for position in TOP}
    for (pid, week), projection in data.projected.items():
        player = data.players.get(pid)
        if (week not in weeks or projection < PLAYING_PROJECTION or player is None or player.position not in TOP
                or (pid, week) in data.injuries):
            continue
        total = totals.get((data.team_at(pid, week + 1), week))
        if total:
            rows[player.position].setdefault(pid, []).append((np.log(total), np.log(projection)))
    out = {}
    for position, by in rows.items():
        parts = [np.array(r) - np.mean(r, axis=0) for r in by.values() if len(r) >= 3]
        if parts:
            x, y = np.concatenate(parts).T
            out[f"proj follows line {position}"] = float(x @ y / (x @ x)) if x @ x > 0 else float("nan")
    return out


def absences(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    weeks = sorted(set(weeks))
    played = {(team, w) for (team, w) in data.scores if w in weeks}
    shows = 0
    for (pid, week), projection in data.projected.items():
        if (week not in weeks or projection < PLAYING_PROJECTION or (pid, week) in data.box or (pid, week) in data.injuries
                or data.players[pid].position not in TOP):            # K and D/ST have no box lines
            continue
        team = data.team_at(pid, week + 1)
        shows += (team, week) in played
    flicker = sum(1 for (pid, week), projection in data.projected.items()
                  if week in weeks and projection <= ZERO_PROJECTION and (pid, week) in data.box
                  and (pid, week) not in data.injuries and data.projected.get((pid, week - 1), 0.0) >= PLAYING_PROJECTION)
    scores = [points for (team, w), points in data.scores.items() if w in weeks]
    late = [points for (team, w), points in data.scores.items() if w in weeks and w >= LATE_WEEK]
    return {"untagged no-shows / week": shows / max(1, len(weeks)),
            "untagged zero projections / week": flicker / max(1, len(weeks)),
            f"team score <= {LOW_SCORE} %": float(100 * np.mean([p <= LOW_SCORE for p in scores])) if scores else float("nan"),
            f"team score <= {LOW_SCORE} % wk {LATE_WEEK}+": float(100 * np.mean([p <= LOW_SCORE for p in late])) if late else float("nan")}
