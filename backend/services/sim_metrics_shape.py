"""Simulated seasons: the shape of a whole season, read alike on a real one and a simulated one (sim_eval full mode).

- passing(data): the season's passing leader, team season passing yards (spread, most) and a team-game's spread.
- shape(data): the season's leaders and how a team's work is shared: 4,000-yard passers, 1,000-yard receivers
  and rushers; receiving yards of the #1/#3/#5/#10 WR and TE; receptions #5 and #10; the share of a team's
  targets its top WR, top TE and top five receivers got, its top RB's share of the carries; the
  spread (sd) of yards per target among receivers with MIN_TARGETS or more (real seasons ~1.3).
- standings(data): best and worst record (wins, a tie half), the spread of team season points, and the share
  of games decided by 1-3 points and by 8 or fewer (one score).
"""
from collections import defaultdict

import numpy as np

from services.replay_pool import SeasonData

LEADER_RANKS = (1, 3, 5, 10)
MIN_TARGETS = 80                 # yards per target spread: receivers with a starter's season of targets
MIN_TEAM_TARGETS = 300           # a team with fewer box targets (a partial season) is left out of the shares


def _totals(data: SeasonData) -> tuple[dict, dict]:
    """({player: {stat: season total}}, {player: the team he had the most work for})."""
    totals: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    work: dict[int, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for (pid, week), line in data.box.items():
        for name in ("pass_yds", "targets", "rec", "rec_yds", "carries", "rush_yds"):
            totals[pid][name] += getattr(line, name)
        team = data.team_at(pid, week)
        if team:
            work[pid][team] += line.targets + line.carries + 1
    return totals, {pid: max(teams, key=teams.get) for pid, teams in work.items()}


def _nth(values: list[float], n: int) -> float:
    return sorted(values, reverse=True)[n - 1] if len(values) >= n else 0.0


def passing(data: SeasonData) -> dict[str, float]:
    """The season's passing leader, and the spread of team passing: a team's season yards (sd, max) and a
    team-game's yards (sd), from the QB lines."""
    leader: dict[int, float] = defaultdict(float)
    games: dict[tuple[str, int], float] = defaultdict(float)
    for (pid, week), line in data.box.items():
        if line.pass_yds and data.players.get(pid) and data.players[pid].position == "QB":
            leader[pid] += line.pass_yds
            games[(data.team_at(pid, week + 1), week)] += line.pass_yds
    teams: dict[str, float] = defaultdict(float)
    for (team, _), yards in games.items():
        teams[team] += yards
    if not teams:
        return {}
    return {"pass yds #1": max(leader.values()), "team pass yds sd": float(np.std(list(teams.values()))),
            "team pass yds max": max(teams.values()), "team-game pass yds sd": float(np.std(list(games.values()))),
            "team-game pass yds mean": float(np.mean(list(games.values())))}


def shape(data: SeasonData) -> dict[str, float]:
    totals, team_of = _totals(data)
    position = {pid: data.players[pid].position for pid in totals if pid in data.players}
    out = {**passing(data), "4000-yd passers": float(sum(totals[p]["pass_yds"] >= 4000 for p in position)),
           "1000-yd receivers": float(sum(totals[p]["rec_yds"] >= 1000 for p in position)),
           "1000-yd rushers": float(sum(totals[p]["rush_yds"] >= 1000 for p in position))}
    for pos in ("WR", "TE"):
        yards = [totals[p]["rec_yds"] for p in position if position[p] == pos]
        out.update({f"{pos} rec yds #{n}": _nth(yards, n) for n in LEADER_RANKS})
    catches = [totals[p]["rec"] for p in position]
    out.update({f"receptions #{n}": _nth(catches, n) for n in (5, 10)})
    shares: dict[str, list[float]] = defaultdict(list)
    by_team: dict[str, list[int]] = defaultdict(list)
    for pid in position:
        if pid in team_of:
            by_team[team_of[pid]].append(pid)
    for pids in by_team.values():
        targets = sum(totals[p]["targets"] for p in pids if position[p] != "QB")
        carries = sum(totals[p]["carries"] for p in pids)
        if targets < MIN_TEAM_TARGETS:
            continue
        for pos in ("WR", "TE"):
            top = max((totals[p]["targets"] for p in pids if position[p] == pos), default=0.0)
            shares[f"top {pos} target share"].append(top / targets)
        ranked = sorted((totals[p]["targets"] for p in pids if position[p] != "QB"), reverse=True)
        shares["top-5 target share"].append(sum(ranked[:5]) / targets)
        backs = max((totals[p]["carries"] for p in pids if position[p] == "RB"), default=0.0)
        if carries:
            shares["top RB carry share"].append(backs / carries)
    out.update({name: float(np.mean(values)) for name, values in shares.items()})
    per_target = [totals[p]["rec_yds"] / totals[p]["targets"] for p in position
                  if position[p] in ("WR", "TE") and totals[p]["targets"] >= MIN_TARGETS]
    out["yds/target sd (80+ tgt)"] = float(np.std(per_target)) if len(per_target) > 2 else float("nan")
    return out


def standings(data: SeasonData) -> dict[str, float]:
    wins: dict[str, float] = defaultdict(float)
    points: dict[str, float] = defaultdict(float)
    margins = []
    for game in data.schedule.games:
        home, away = data.scores.get((game.home_team, game.week)), data.scores.get((game.away_team, game.week))
        if home is None or away is None:
            continue
        points[game.home_team] += home
        points[game.away_team] += away
        wins[game.home_team] += 1.0 if home > away else 0.5 if home == away else 0.0
        wins[game.away_team] += 1.0 if away > home else 0.5 if home == away else 0.0
        margins.append(abs(home - away))
    if not margins:
        return {}
    margins = np.array(margins)
    return {"best record (wins)": max(wins.values()), "worst record (wins)": min(wins.values()),
            "team season points sd": float(np.std(list(points.values()))),
            "games by 1-3 %": float(100 * np.mean((margins >= 1) & (margins <= 3))),
            "games by 8 or fewer %": float(100 * np.mean(margins <= 8))}
