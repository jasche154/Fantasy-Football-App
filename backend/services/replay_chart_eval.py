"""Replay League trade chart against what players really produced afterwards. Read-only on the database.

Run from backend/:
    python -m services.replay_chart_eval [--seasons 2018-2025] [--weeks 1,4,7,10,13] [--scoring ppr]
                                         [--teams 12] [--lineups regular,superflex] [--out report.json]

For each real season and week W it builds the point-in-time `Knowledge` (replay_values.knowledge, seed 0)
for a regular lineup (QB, 2 RB, 2 WR, TE, FLEX, K, DST) and a superflex one (the same plus SUPERFLEX), and
scores each skill player's `chart` against his realised value from W to the league's last week (week 17:
14 regular weeks and a 3-round, 6-team playoff):

- target = points over replacement PER GAME HE PLAYED from W on (the user's rule: trade value cannot know
  about an injury that has not happened yet, so games missed to an absence that starts after W are left
  out, never counted as zeros). Scored only with at least MIN_GAMES games played (one-game samples dropped).
- a player out at W (Knowledge's `out`): the absence known at W does count, and only it: target = his rate
  in the games he played after coming back x the share of his team's remaining games he did NOT miss to
  that absence (byes are not games; a suspension known at W is such an absence, a later one is not). Back
  at no later week: 0. Back but fewer than MIN_GAMES games afterwards: dropped.
- replacement: league.replacement_levels on the realised rates (players with MIN_GAMES+ games) for the
  lineup's teams, starters, FLEX, SUPERFLEX and bench.

Population: skill players whose healthy_chart is at least MIN_CHART (about the 110th pick: rosterable). The
report, per lineup: Spearman correlation of chart and target (mean over season-weeks), overall and within
each position; calibration: mean target per chart band and position (a 700 QB vs a 700 WR); a held-out
calibration error (leave-season-out: a position-blind band curve fitted on the other seasons, RMSE on the
held-out one); the same for the players out at W; and how many player-weeks each rule scored, dropped or
changed. Pure numbers in, numbers out: `evaluate` takes rows, `rows_for` builds them.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass

import numpy as np

from services import replay_adp_format, replay_state as rs, replay_values as rv
from services.league import replacement_levels
from services.replay_pool import SKILL, SeasonData

log = logging.getLogger(__name__)

MIN_GAMES = 3                  # games played after W (after the return, for a player out at W) to be scored
MIN_CHART = 50.0               # population: healthy_chart at least this
BANDS = (800.0, 600.0, 400.0, 250.0, 100.0, MIN_CHART)     # chart bands, lower edges, best first
WEEKS = (1, 4, 7, 10, 13)
SEED = 0                       # the injury news noise (replay_news): one fixed draw
REGULAR = dict(rs.DEFAULT_STARTERS)
LINEUPS = {"regular": REGULAR, "superflex": {**REGULAR, "SUPERFLEX": 1}}
TIERS = (1, 7, 13, 25, 37)     # position-rank tiers (first rank of each) for the held-out residuals


@dataclass(frozen=True)
class Row:
    season: int
    week: int
    lineup: str
    player_id: int
    name: str
    position: str
    chart: float
    healthy_chart: float
    out: bool
    target: float
    rule: str                  # healthy | healthy_later_missed | out_back | out_season
    pos_rank: int = 0          # by chart within his position that week


def _seasons(text: str) -> list[int]:
    if "-" in text:
        first, last = text.split("-")
        return list(range(int(first), int(last) + 1))
    return [int(s) for s in text.split(",")]


def settings_for(season: int, lineup: str, scoring: str, teams: int, bench: int = 6) -> rs.Settings:
    starters = LINEUPS[lineup]
    return rs.Settings(season=season, teams=teams, scoring=scoring, starters=starters, playoff_teams=6,
                       bench=bench, adp_format=replay_adp_format.wanted(scoring, None, starters))


def _team_weeks(data: SeasonData, team: str | None, first: int, last: int) -> list[int]:
    return [w for w in range(first, last + 1) if data.schedule.opponent(team, w) is not None]


def realised(data: SeasonData, week: int, end_week: int, out: Mapping[int, bool],
             league) -> tuple[dict[int, tuple[float, str]], Counter]:
    """({player: (target, rule)}, counts of each rule incl. the dropped ones) for the skill players."""
    rates: dict[int, tuple[float, int]] = {}            # (points per game played from `start`, games)
    starts: dict[int, int] = {}
    for pid, player in data.players.items():
        if player.position not in SKILL:
            continue
        start = week
        if out.get(pid):                                # the known absence ends at his next real game
            start = next((w for w in range(week, end_week + 1) if (pid, w) in data.actual), end_week + 1)
        games = [data.actual[(pid, w)] for w in range(start, end_week + 1) if (pid, w) in data.actual]
        starts[pid] = start
        if games:
            rates[pid] = (sum(games) / len(games), len(games))
    level = replacement_levels(((data.players[pid].position, rate) for pid, (rate, n) in rates.items()
                                if n >= MIN_GAMES), league)
    found: dict[int, tuple[float, str]] = {}
    counts: Counter = Counter()
    for pid, start in starts.items():
        position, (rate, n) = data.players[pid].position, rates.get(pid, (0.0, 0))
        if out.get(pid) and start > end_week:
            found[pid] = (0.0, "out_season")
            counts["out_season"] += 1
            continue
        if n < MIN_GAMES:
            counts["out_dropped" if out.get(pid) else "healthy_dropped"] += 1
            continue
        over = rate - level[position]
        team_weeks = _team_weeks(data, data.team_at(pid, week), week, end_week)
        if out.get(pid):
            missed = sum(w < start for w in team_weeks)
            found[pid] = (over * (1 - missed / max(1, len(team_weeks))), "out_back")
        else:
            later = sum(w >= start and (pid, w) not in data.actual for w in team_weeks)
            found[pid] = (over, "healthy_later_missed" if later else "healthy")
        counts[found[pid][1]] += 1
    return found, counts


def rows_for(data: SeasonData, lineup: str, settings: rs.Settings, weeks: Sequence[int]) -> tuple[list[Row], Counter]:
    """The scored rows of one season and lineup, and the rule counts (population only)."""
    league, end_week = rv.league_settings(settings), settings.end_week
    rows: list[Row] = []
    counts: Counter = Counter()
    for week in weeks:
        know = rv.knowledge(data, league, week, end_week, SEED)
        pool = [v for v in know.values if v.position in SKILL and v.healthy_chart >= MIN_CHART]
        targets, _ = realised(data, week, end_week, {v.player_id: v.out for v in pool}, league)
        in_pool = {v.player_id for v in pool}
        seen: Counter = Counter()
        for v in pool:
            seen[v.position] += 1
            if v.player_id not in targets:
                counts["out_dropped" if v.out else "healthy_dropped"] += 1
                continue
            target, rule = targets[v.player_id]
            counts[rule] += 1
            rows.append(Row(data.season, week, lineup, v.player_id, v.name, v.position, v.chart, v.healthy_chart,
                            v.out, round(target, 3), rule, seen[v.position]))
        counts["population"] += len(in_pool)
    return rows, counts


# ---------------- metrics ----------------

def _ranks(values: Sequence[float]) -> np.ndarray:
    """1-based ranks, ties averaged."""
    array = np.asarray(values, dtype=float)
    order = array.argsort(kind="mergesort")
    ranks = np.empty(len(array))
    ranks[order] = np.arange(1, len(array) + 1)
    for value in np.unique(array):
        tied = array == value
        if tied.sum() > 1:
            ranks[tied] = ranks[tied].mean()
    return ranks


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3:
        return None
    a, b = _ranks(xs), _ranks(ys)
    if a.std() == 0 or b.std() == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def _mean_spearman(rows: Sequence[Row], keep: Callable[[Row], bool]) -> float | None:
    """Mean over (season, week) of the Spearman correlation among the kept rows."""
    groups: dict[tuple[int, int], list[Row]] = defaultdict(list)
    for row in rows:
        if keep(row):
            groups[(row.season, row.week)].append(row)
    found = [r for r in (spearman([x.chart for x in g], [x.target for x in g]) for g in groups.values())
             if r is not None]
    return round(float(np.mean(found)), 3) if found else None


def band(chart: float) -> str:
    low = next((edge for edge in BANDS if chart >= edge), 0.0)
    above = [edge for edge in BANDS if edge > low]
    return f"{low:.0f}+" if not above else f"{low:.0f}-{min(above):.0f}"


def calibration(rows: Iterable[Row]) -> dict[str, dict[str, tuple[float, int]]]:
    """{band: {position | "ALL": (mean target, players)}}."""
    cells: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        cells[band(row.chart)][row.position].append(row.target)
        cells[band(row.chart)]["ALL"].append(row.target)
    return {b: {p: (round(float(np.mean(v)), 2), len(v)) for p, v in sorted(by.items())}
            for b, by in sorted(cells.items(), key=lambda item: -float(item[0].split("+")[0].split("-")[0]))}


def held_out_error(rows: Sequence[Row], scored: Callable[[Row], bool] = lambda row: True) -> dict:
    """Leave-season-out: per held-out season, a position-blind curve (mean target per chart band) fitted on
    the other seasons; RMSE of the held-out targets against it, and each position's (and position-rank
    tier's) mean miss (bias). `scored`: which held-out rows are scored (the curve always uses every row)."""
    seasons = sorted({r.season for r in rows})
    errors: list[float] = []
    bias: dict[str, list[float]] = defaultdict(list)
    tiers: dict[str, list[float]] = defaultdict(list)
    for held in seasons:
        curve: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            if row.season != held:
                curve[band(row.chart)].append(row.target)
        means = {b: float(np.mean(v)) for b, v in curve.items()}
        for row in rows:
            if row.season == held and band(row.chart) in means and scored(row):
                miss = row.target - means[band(row.chart)]
                errors.append(miss * miss)
                bias[row.position].append(miss)
                tiers[f"{row.position}{tier(row.pos_rank)}"].append(miss)
    if not errors:
        return {}
    return {"rmse": round(float(np.sqrt(np.mean(errors))), 3),
            **{f"bias_{p}": round(float(np.mean(v)), 3) for p, v in sorted(bias.items())},
            "tiers": {name: (round(float(np.mean(v)), 2), len(v)) for name, v in sorted(tiers.items())}}


def tier(pos_rank: int) -> str:
    first = max(t for t in TIERS if t <= max(1, pos_rank))
    later = [t for t in TIERS if t > first]
    return f"{first}-{later[0] - 1}" if later else f"{first}+"


def realised_share(rows: Sequence[Row]) -> float | None:
    """Out players' targets over what healthy players of the same healthy_chart band produced: the share of
    his value the known absence really cost him (compare with their mean chart / healthy_chart)."""
    healthy: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        if not row.out:
            healthy[band(row.healthy_chart)].append(row.target)
    means = {b: float(np.mean(v)) for b, v in healthy.items()}
    out = [(row.target, means[band(row.healthy_chart)]) for row in rows
           if row.out and band(row.healthy_chart) in means]
    expected = sum(e for _, e in out)
    return round(sum(t for t, _ in out) / expected, 3) if out and expected > 0 else None


def evaluate(rows: Sequence[Row]) -> dict:
    """The report for one lineup's rows."""
    positions = sorted({r.position for r in rows})
    out_rows = [r for r in rows if r.out]
    return {
        "players": len(rows),
        "spearman": _mean_spearman(rows, lambda r: True),
        "spearman_by_position": {p: _mean_spearman(rows, lambda r, p=p: r.position == p) for p in positions},
        "held_out": held_out_error(rows),
        "calibration": calibration(rows),
        "out": {"players": len(out_rows), "spearman": _mean_spearman(out_rows, lambda r: True),
                "held_out": held_out_error(rows, lambda r: r.out), "realised_share": realised_share(rows),
                "calibration": calibration(out_rows),
                "chart_share": round(float(np.mean([r.chart / r.healthy_chart for r in out_rows])), 3)
                if out_rows else None},
    }


def run(seasons: Sequence[int], weeks: Sequence[int], scoring: str, teams: int,
        lineups: Sequence[str], bench: int = 6) -> dict:
    from database.db import SessionLocal
    from services import replay_pool

    rows: dict[str, list[Row]] = defaultdict(list)
    counts: dict[str, Counter] = defaultdict(Counter)
    with SessionLocal() as session:
        for season in seasons:
            for lineup in lineups:
                settings = settings_for(season, lineup, scoring, teams, bench)
                data = replay_pool.load_season_data(session, season, scoring, None, settings.adp_format)
                found, counted = rows_for(data, lineup, settings, weeks)
                rows[lineup].extend(found)
                counts[lineup].update(counted)
                log.info("%s %s: %d rows", season, lineup, len(found))
            replay_pool.clear_cache()
    return {lineup: {**evaluate(rows[lineup]), "rules": dict(counts[lineup]),
                     "rows": [asdict(r) for r in rows[lineup]]} for lineup in lineups}


def compare(before: Mapping[str, dict], after: Mapping[str, dict]) -> dict:
    """Two saved reports (--out) scored on the player-weeks both of them scored, per lineup: a chart change
    moves players across the MIN_CHART population edge, so only common rows compare like for like."""
    found = {}
    for lineup in before.keys() & after.keys():
        rows = [{(r["season"], r["week"], r["player_id"]): Row(**r) for r in report[lineup]["rows"]}
                for report in (before, after)]
        common = rows[0].keys() & rows[1].keys()
        found[lineup] = {name: {**evaluate([table[key] for key in sorted(common)]), "rules": {}}
                         for name, table in zip(("before", "after"), rows)}
    return found


def _log_report(report: Mapping[str, dict]) -> None:
    for lineup, result in report.items():
        log.info("\n== %s: %d player-weeks; rules %s", lineup, result["players"], result["rules"])
        log.info("Spearman overall %s; by position %s", result["spearman"], result["spearman_by_position"])
        log.info("Held-out calibration %s", result["held_out"])
        for title, table in (("all", result["calibration"]), ("out at W", result["out"]["calibration"])):
            log.info("Mean target per game over replacement by chart band (%s):", title)
            for name, cells in table.items():
                log.info("  %-8s %s", name, "  ".join(f"{p} {m:+.2f} (n={n})" for p, (m, n) in cells.items()))
        log.info("Out at W: %d, Spearman %s, chart / healthy %s, realised share %s, held-out %s",
                 result["out"]["players"], result["out"]["spearman"], result["out"]["chart_share"],
                 result["out"]["realised_share"], result["out"]["held_out"])


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", default="2018-2025")
    parser.add_argument("--weeks", default=",".join(map(str, WEEKS)))
    parser.add_argument("--scoring", default="ppr", choices=("ppr", "half", "std"))
    parser.add_argument("--teams", type=int, default=12)
    parser.add_argument("--lineups", default="regular,superflex")
    parser.add_argument("--bench", type=int, default=6, help="bench spots (0: value over the last starter)")
    parser.add_argument("--out", help="write the report (rows included) as JSON here")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="score two saved reports on their common player-weeks (nothing is rebuilt)")
    args = parser.parse_args(argv)
    if args.compare:
        before, after = (json.loads(Path(path).read_text()) for path in args.compare)
        for lineup, pair in compare(before, after).items():
            for name, result in pair.items():
                log.info("#### %s: %s", lineup, name)
                _log_report({lineup: result})
        return
    report = run(_seasons(args.seasons), [int(w) for w in args.weeks.split(",")], args.scoring, args.teams,
                 args.lineups.split(","), args.bench)
    _log_report(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
