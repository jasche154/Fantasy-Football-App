"""Simulated seasons: the numbers that say whether a season looks like real football (used by sim_eval).

Every metric reads a SeasonData the same way, so a real season and a simulated one are measured alike.
Tiers are preseason ADP position ranks (QB / TE: 1-12, 13-24; RB / WR: 1-12, 13-24, 25-36).

- weekly(data, weeks): points quantiles (q10 / q50 / q90) per tier, the share of tiered games of 25+ and 30+
  points, pre-game projection vs actual (RMSE and Spearman rank correlation over skill games projected
  PROJECTED_MIN or more), the same-team QB-WR and QB-TE correlation of (actual - projection), tiered players
  sitting out per week by position (team plays, no game), and team skill points per game (mean, spread
  across games, spread of season means), K and D/ST points per game, and the count of skill games of 25+ /
  30+ / 35+ points (BIG_GAMES; ADP-free), and `injuries`.
- injuries(data, weeks): for the top-150 ADP skill players, by position: new injuries per game played (he played
  a game in `weeks` and missed his team's next one with an injury tag, any of Q/D/O/IR/PUP: the sim_injuries fit's
  own definition, since a real player ruled out on game day often keeps his Q/D) and the share of his team's
  games in `weeks` he missed with an injury tag.
- season(data): ADP position rank vs final position rank (Spearman), breakouts (top-12 finishers drafted
  outside the top 24 or undrafted), busts (top-12 ADP finishing outside the top 24), games missed per
  top-150 ADP player, and the leaderboard.
- leaderboard(data, weeks): ADP-free: mean points per game played of the top 1 / 6 / 12 / 24 finishers by
  position (LEADERS), so a flat simulated season shows whether or not our ADP is right.
- adp_agreement(data, real, rookies): our ADP vs the real ADP (Spearman: the real top 150; the real-drafted
  rookies); expert_agreement(data): the week-0 expert position ranks vs the ADP position ranks (top 150).
"""
from collections import defaultdict
from collections.abc import Iterable

import numpy as np

from services.replacement_eval import spearman  # noqa: F401  (tied values share their average rank)
from services.replay_pool import SeasonData
from services.sim_injuries_fit import _hurt

TIER_SIZE = 12
TIERS = {"QB": 2, "RB": 3, "WR": 3, "TE": 2}
PROJECTED_MIN = 5.0
SKILL = ("QB", "RB", "WR", "TE")
TOP_FINISH, BREAKOUT_ADP, BUST_FINISH = 12, 24, 24
TOP_ADP_FOR_MISSED = 150
BIG_GAMES = (25, 30, 35)         # skill games of this many points or more, counted
LEADERS = (1, 6, 12, 24)         # leaderboard: mean points per game of the top N finishers at a position


def adp_ranks(data: SeasonData) -> dict[int, int]:
    """{player: ADP rank at his position}."""
    ranks: dict[int, int] = {}
    for position in (*SKILL, "K", "DST"):
        drafted = sorted((p.adp, p.player_id) for p in data.players.values() if p.position == position and p.adp)
        ranks.update({pid: rank for rank, (_, pid) in enumerate(drafted, start=1)})
    return ranks


def tiers(data: SeasonData) -> dict[int, str]:
    out = {}
    for pid, rank in adp_ranks(data).items():
        position = data.players[pid].position
        if position in TIERS and rank <= TIERS[position] * TIER_SIZE:
            out[pid] = f"{position}{(rank - 1) // TIER_SIZE + 1}"
    return out


def _teams(data: SeasonData) -> dict[tuple[int, int], str]:
    return {(pid, week): team for pid, games in data.team_by_week.items() for week, team in games}


def _qb_pairs(data: SeasonData, weeks: set[int], teams: dict) -> dict[str, list[tuple[float, float]]]:
    passer: dict[tuple[str, int], tuple[int, int]] = {}
    for (pid, week), line in data.box.items():
        key = (teams.get((pid, week)), week)
        if week in weeks and data.players[pid].position == "QB" and line.pass_yds > passer.get(key, (0, -1))[1]:
            passer[key] = (pid, line.pass_yds)
    pairs: dict[str, list] = defaultdict(list)
    for (pid, week), points in data.actual.items():
        position = data.players[pid].position
        qb = passer.get((teams.get((pid, week)), week))
        projection = data.projected.get((pid, week), 0.0)
        if week in weeks and position in ("WR", "TE") and qb and projection >= PROJECTED_MIN:
            qb_projection = data.projected.get((qb[0], week))
            if qb_projection:
                pairs[position].append((data.actual[(qb[0], week)] - qb_projection, points - projection))
    return pairs


def weekly(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    weeks = set(weeks)
    tier_of, teams = tiers(data), _teams(data)
    out = {**_scores(data, weeks, tier_of), **_projection_fit(data, weeks)}
    for position, pairs in _qb_pairs(data, weeks, teams).items():
        out[f"QB-{position} corr"] = float(np.corrcoef(np.array(pairs).T)[0, 1])
    sitting: dict[str, int] = defaultdict(int)
    for pid in tier_of:
        for week in weeks:
            team = data.team_at(pid, week)
            if team and data.schedule.opponent(team, week) and (pid, week) not in data.actual:
                sitting[data.players[pid].position] += 1
    for position in SKILL:
        out[f"sitting/wk {position}"] = sitting[position] / max(1, len(weeks))
    return {**out, **_team_scores(data, weeks, teams), **injuries(data, weeks)}


def injuries(data: SeasonData, weeks: Iterable[int]) -> dict[str, float]:
    """New injuries per game played and missed share, by position (see the module docstring)."""
    weeks = set(weeks)
    played, new, games, missed = (defaultdict(int) for _ in range(4))
    top = sorted((p.adp, pid) for pid, p in data.players.items() if p.adp and p.position in SKILL)[:TOP_ADP_FOR_MISSED]
    for _, pid in top:
        position = data.players[pid].position
        team_games = [w for w in range(1, data.last_week + 1) if data.schedule.opponent(data.team_at(pid, w), w)]
        hurt = [(pid, w) not in data.actual and _hurt(data.injuries.get((pid, w))) for w in team_games]
        for i, week in enumerate(team_games):
            if week not in weeks:
                continue
            games[position] += 1
            missed[position] += hurt[i]
            if (pid, week) in data.actual and i + 1 < len(team_games):
                played[position] += 1
                new[position] += hurt[i + 1]
    out = {}
    for position in SKILL:
        out[f"new inj/game {position}"] = new[position] / max(1, played[position])
        out[f"missed share {position}"] = missed[position] / max(1, games[position])
    return out


def _scores(data: SeasonData, weeks: set[int], tier_of: dict) -> dict[str, float]:
    """Weekly points by tier (q10/q50/q90), the share of 25+ and 30+ games, and the count of big games."""
    out: dict[str, float] = {}
    by_tier: dict[str, list[float]] = defaultdict(list)
    for (pid, week), points in data.actual.items():
        if week in weeks and pid in tier_of:
            by_tier[tier_of[pid]].append(points)
    for tier, points in sorted(by_tier.items()):
        for q in (10, 50, 90):
            out[f"{tier} q{q}"] = float(np.percentile(points, q))
    tiered = [p for values in by_tier.values() for p in values]
    out["25+ share %"] = 100 * float(np.mean([p >= 25 for p in tiered]))
    out["30+ share %"] = 100 * float(np.mean([p >= 30 for p in tiered]))
    skill = [points for (pid, week), points in data.actual.items() if week in weeks and data.players[pid].position in SKILL]
    for line in BIG_GAMES:
        out[f"{line}+ games"] = float(sum(1 for p in skill if p >= line))
    return out


def _projection_fit(data: SeasonData, weeks: set[int]) -> dict[str, float]:
    """Projection RMSE, bias and rank correlation per skill position and overall."""
    out: dict[str, float] = {}
    for position in (*SKILL, "all"):
        pairs = [(data.projected[k], v) for k, v in data.actual.items() if k[1] in weeks
                 and data.projected.get(k, 0) >= PROJECTED_MIN and data.players[k[0]].position in SKILL
                 and position in ("all", data.players[k[0]].position)]
        if pairs:
            proj, act = np.array(pairs).T
            out[f"proj RMSE {position}"] = float(np.sqrt(np.mean((proj - act) ** 2)))
            out[f"actual - proj {position}"] = float(np.mean(act - proj))
            out[f"proj rank r {position}"] = spearman(proj, act)
    return out


def _team_scores(data: SeasonData, weeks: set[int], teams: dict) -> dict[str, float]:
    """Team skill points a game (mean, sd, sd of season means) and K/DST points (mean, sd)."""
    out: dict[str, float] = {}
    team_points: dict[tuple[str, int], float] = defaultdict(float)
    special: dict[str, list[float]] = defaultdict(list)
    for (pid, week), points in data.actual.items():
        position = data.players[pid].position
        if week in weeks and position in SKILL and (pid, week) in teams:
            team_points[(teams[(pid, week)], week)] += points
        elif week in weeks and position in ("K", "DST"):
            special[position].append(points)
    values = np.array(list(team_points.values()))
    season_means = defaultdict(list)
    for (team, _), points in team_points.items():
        season_means[team].append(points)
    out["team pts mean"], out["team pts sd"] = float(values.mean()), float(values.std())
    out["team season sd"] = float(np.std([np.mean(v) for v in season_means.values()]))
    for position, points in special.items():
        out[f"{position} mean"], out[f"{position} sd"] = float(np.mean(points)), float(np.std(points))
    return out


def season(data: SeasonData) -> dict[str, float]:
    ranks = adp_ranks(data)
    totals: dict[int, float] = defaultdict(float)
    for (pid, _), points in data.actual.items():
        totals[pid] += points
    out: dict[str, float] = {}
    breakouts = busts = 0
    for position in SKILL:
        finish = sorted((pid for pid in data.players if data.players[pid].position == position),
                        key=lambda pid: (-totals.get(pid, 0.0), pid))
        place = {pid: i for i, pid in enumerate(finish, start=1)}
        drafted = [pid for pid in ranks if data.players[pid].position == position]
        out[f"ADP vs finish r {position}"] = spearman([ranks[p] for p in drafted], [place[p] for p in drafted])
        breakouts += sum(1 for pid in finish[:TOP_FINISH] if ranks.get(pid, 10 ** 6) > BREAKOUT_ADP)
        busts += sum(1 for pid in drafted if ranks[pid] <= TOP_FINISH and place[pid] > BUST_FINISH)
    out["breakouts"], out["busts"] = float(breakouts), float(busts)
    out.update(leaderboard(data))
    top = sorted((p.adp, p.player_id) for p in data.players.values() if p.adp and p.position in SKILL)[:TOP_ADP_FOR_MISSED]
    missed = []
    for _, pid in top:
        team = data.team_at(pid, data.last_week + 1)
        games = [w for w in range(1, data.last_week + 1) if data.schedule.opponent(team, w)]
        missed.append(sum(1 for w in games if (pid, w) not in data.actual))
    out["games missed / top-150"] = float(np.mean(missed))
    return out


def leaderboard(data: SeasonData, weeks: Iterable[int] | None = None) -> dict[str, float]:
    """ADP-free: mean points per game played of the top N finishers (points over `weeks`, default all) at
    each position."""
    weeks = set(weeks) if weeks is not None else None
    totals: dict[int, float] = defaultdict(float)
    games: dict[int, int] = defaultdict(int)
    for (pid, week), points in data.actual.items():
        if weeks is None or week in weeks:
            totals[pid] += points
            games[pid] += 1
    out = {}
    for position in SKILL:
        finish = sorted((pid for pid in totals if data.players[pid].position == position), key=lambda pid: (-totals[pid], pid))
        for n in LEADERS:
            out[f"{position} top-{n} ppg"] = float(np.mean([totals[pid] / games[pid] for pid in finish[:n]]))
    return out


def qb_starts(data: SeasonData) -> dict[int, list[int]]:
    """{QB: the weeks he threw the most passing yards for his team}."""
    best: dict[tuple[str | None, int], tuple[int, float]] = {}
    teams = _teams(data)
    for (pid, week), line in data.box.items():
        player = data.players.get(pid)
        key = (teams.get((pid, week)), week)
        if player and player.position == "QB" and line.pass_yds > best.get(key, (0, 0.0))[1]:
            best[key] = (pid, line.pass_yds)
    out: dict[int, list[int]] = defaultdict(list)
    for (_, week), (pid, _) in best.items():
        out[pid].append(week)
    return out


def _totals(data: SeasonData) -> dict[int, float]:
    totals: dict[int, float] = defaultdict(float)
    for (pid, _), points in data.actual.items():
        totals[pid] += points
    return totals


def rookies(data: SeasonData, ids: set[int]) -> dict[str, float]:
    """Rookies (`ids`): ADP vs final rank among drafted rookies (Spearman), top-24 position finishes by rookies,
    and of the rookie QBs, how many started week 1 and how many of the others started a game later."""
    totals, ranks = _totals(data), adp_ranks(data)
    top24 = 0
    for position in SKILL:
        finish = sorted((pid for pid in data.players if data.players[pid].position == position),
                        key=lambda pid: (-totals.get(pid, 0.0), pid))
        top24 += sum(1 for pid in finish[:BUST_FINISH] if pid in ids)
    drafted = [pid for pid in ids if pid in ranks and data.players[pid].position in SKILL]
    starts = qb_starts(data)
    qbs = [pid for pid in ids if pid in data.players and data.players[pid].position == "QB"]
    first = [pid for pid in qbs if 1 in starts.get(pid, ())]
    later = [pid for pid in qbs if pid not in first and starts.get(pid)]
    return {"rookie ADP vs finish r": spearman([data.players[p].adp for p in drafted], [-totals.get(p, 0.0) for p in drafted]),
            "rookie top-24 finishes": float(top24), "rookie QB wk1 starters": float(len(first)),
            "rookie QB later starters": float(len(later))}


def adp_agreement(data: SeasonData, real: SeasonData, rookies: Iterable[int] = (),
                  top: int = TOP_ADP_FOR_MISSED) -> dict[str, float]:
    """Spearman of this season's ADP vs the real ADP among the real top `top`, and among the `rookies` the real
    list drafted (no ADP here = last)."""
    def mine(pid: int) -> float:
        return data.players[pid].adp if pid in data.players and data.players[pid].adp else 10 ** 4

    board = sorted((p.adp, pid) for pid, p in real.players.items() if p.adp and p.position in SKILL)[:top]
    drafted = sorted(pid for pid in rookies if pid in real.players and real.players[pid].adp)
    return {"ADP vs real ADP r (top-150)": spearman([adp for adp, _ in board], [mine(pid) for _, pid in board]),
            "rookie ADP vs real ADP r": spearman([real.players[p].adp for p in drafted], [mine(p) for p in drafted])}


def expert_agreement(data: SeasonData, top: int = TOP_ADP_FOR_MISSED) -> dict[str, float]:
    """Spearman of the preseason (week 0) expert position rank vs the ADP position rank among the top `top` ADP
    skill players with both (nan without week-0 ranks)."""
    ranks = adp_ranks(data)
    experts = {pid: rank for (pid, week), rank in data.expert_ranks.items() if week == 0}
    board = sorted((p.adp, pid) for pid, p in data.players.items() if p.adp and p.position in SKILL)[:top]
    both = [pid for _, pid in board if pid in experts]
    return {"expert vs ADP r (week 0)": spearman([ranks[p] for p in both], [experts[p] for p in both])}
