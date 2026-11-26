"""Replay League trade chart against what players really produced afterwards. Read-only on the database.

Run from backend/:
    python -m services.replay_chart_eval [--seasons 2018-2025] [--weeks 1,4,7,10,13] [--scoring ppr]
                                         [--teams 12] [--lineups regular,superflex] [--out report.json]

For each real season and week W it builds the point-in-time `Knowledge` (replay_values.knowledge, seed 0)
for a regular lineup (QB, 2 RB, 2 WR, TE, FLEX, K, DST) and a superflex one (the same plus SUPERFLEX), and
scores each skill player's `chart` against his realised value from W to the league's last week (week 17:
14 regular weeks and a 3-round, 6-team playoff):

- target = points over replacement per game from W on (`_games`): a game he played counts its points; a
  team game he missed counts 0 (a benching, a lost role, a healthy scratch: busts are what the chart must
  price), except while an injury or suspension that started after W is open (a hard tag since his last
  game, as replay_pool.hard_tag_returns runs a streak): those games are left out, because trade value
  cannot know about an injury that has not happened yet (the user's rule). A bye is no game. Scored with at
  least MIN_GAMES counted games (one-game samples dropped).
- a player out at W (Knowledge's `out`): the absence known at W counts, and only it: target = his rate from
  his next real game on (the same rule, so a later injury is left out) x the share of his team's remaining
  games he did NOT miss to that absence. Back with fewer than MIN_GAMES counted games: his games from
  before W are added to them. Back at no later week: 0.
- replacement: league.replacement_levels on everyone's rate from W (MIN_GAMES+ counted games) for the
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
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass


from services import replay_adp_format, replay_chart_injury, replay_news, replay_state as rs, replay_values as rv
from services.league import replacement_levels
from services.replay_chart_eval_stats import (  # noqa: F401 (re-exported)
    BANDS, MIN_CHART, TIERS, Row, band, calibration, evaluate, held_out_error, realised_share, spearman,
    tier,
)
from services.replay_data import parse_seasons
from services.replay_pool import SKILL, SeasonData
from services.replay_value_eval import DEFAULT_DB

log = logging.getLogger(__name__)

MIN_GAMES = 3                  # games played after W (after the return, for a player out at W) to be scored
WEEKS = (1, 4, 7, 10, 13)
SEED = 0                       # the injury news noise (replay_news): one fixed draw
REGULAR = dict(rs.DEFAULT_STARTERS)
LINEUPS = {"regular": REGULAR, "superflex": {**REGULAR, "SUPERFLEX": 1}}


def settings_for(season: int, lineup: str, scoring: str, teams: int, bench: int = 6) -> rs.Settings:
    starters = LINEUPS[lineup]
    return rs.Settings(season=season, teams=teams, scoring=scoring, starters=starters, playoff_teams=6,
                       bench=bench, adp_format=replay_adp_format.wanted(scoring, None, starters))


def _team_game(data: SeasonData, player_id: int, week: int) -> bool:
    return data.schedule.opponent(data.team_at(player_id, week), week) is not None


@dataclass(frozen=True)
class Games:
    points: tuple[float, ...]      # every game that counts: real points, 0 for a missed one
    zeros: int                     # missed team games counted as 0 (no stat row, no open injury)
    excused: int                   # missed team games left out (an injury or suspension streak open)


def _games(data: SeasonData, player_id: int, first: int, last: int) -> Games:
    """His games from `first` to `last` under the target's rule (module docstring)."""
    points: list[float] = []
    zeros = excused = 0
    injured = False                                     # a hard tag since his last game
    for week in range(first, last + 1):
        if (player_id, week) in data.actual:
            points.append(data.actual[(player_id, week)])
            injured = False
            continue
        tag = data.injuries.get((player_id, week))
        injured = injured or (tag is not None and tag.hard)
        if not _team_game(data, player_id, week):
            continue                                    # a bye
        if injured:
            excused += 1
        else:
            points.append(0.0)
            zeros += 1
    return Games(tuple(points), zeros, excused)


def _rate(games: Games) -> float:
    return sum(games.points) / len(games.points)


def target(data: SeasonData, player_id: int, week: int, end_week: int, out: bool,
           level: Mapping[str, float]) -> tuple[float, str] | None:
    """(target, rule) for one player valued before `week`, or None when too few games to score him."""
    replacement = level[data.players[player_id].position]
    if not out:
        games = _games(data, player_id, week, end_week)
        if len(games.points) < MIN_GAMES:
            return None
        rule = "healthy_later_injury" if games.excused else "healthy_zero_games" if games.zeros else "healthy"
        return _rate(games) - replacement, rule
    back = next((w for w in range(week, end_week + 1) if (player_id, w) in data.actual), None)
    if back is None:
        return 0.0, "out_season"
    team_weeks = [w for w in range(week, end_week + 1) if _team_game(data, player_id, w)]
    share = 1 - sum(w < back for w in team_weeks) / max(1, len(team_weeks))
    games, rule = _games(data, player_id, back, end_week), "out_back"
    if len(games.points) < MIN_GAMES:                   # back late: his rate before the absence too
        before = _games(data, player_id, 1, week - 1)
        games, rule = Games(before.points + games.points, 0, 0), "out_back_short"
    return (_rate(games) - replacement) * share, rule


def realised(data: SeasonData, week: int, end_week: int, out: Mapping[int, bool],
             league) -> dict[int, tuple[float, str]]:
    """{player: (target, rule)} for the skill players in `out` (True = out at `week`) that can be scored."""
    rates = {}
    for pid, player in data.players.items():
        if player.position in SKILL:
            games = _games(data, pid, week, end_week)
            if len(games.points) >= MIN_GAMES:
                rates[pid] = _rate(games)
    level = replacement_levels(((data.players[pid].position, rate) for pid, rate in rates.items()), league)
    found = {pid: target(data, pid, week, end_week, is_out, level) for pid, is_out in out.items()}
    return {pid: value for pid, value in found.items() if value is not None}


def rows_for(data: SeasonData, lineup: str, settings: rs.Settings, weeks: Sequence[int]) -> tuple[list[Row], Counter]:
    """The scored rows of one season and lineup, and the rule counts (population only)."""
    league, end_week = rv.league_settings(settings), settings.end_week
    rows: list[Row] = []
    counts: Counter = Counter()
    for week in weeks:
        know = rv.knowledge(data, league, week, end_week, SEED)
        pool = [v for v in know.values if v.position in SKILL and v.healthy_chart >= MIN_CHART]
        targets = realised(data, week, end_week, {v.player_id: v.out for v in pool}, league)
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


ABSENCE_WEEKS = range(1, 11)    # weeks an absence is measured from (week 10 still sees 7+ games to its end)
NEVER = -1                     # absences_unreported: never back that season


def absences_unreported(data: SeasonData) -> Counter:
    """{team games missed counting this week (NEVER: not back that season): player-weeks} for the skill
    players out with no hard tag (no projection, no news report: replay_chart_injury's unreported case), in
    ABSENCE_WEEKS. 0 = he played that week after all."""
    priors = rv._priors(data)
    found: Counter = Counter()
    for pid, player in data.players.items():
        if player.position not in SKILL or priors.get(pid, 0.0) <= 0:
            continue
        for week in ABSENCE_WEEKS:
            tag = data.injuries.get((pid, week))
            if (tag is not None and tag.hard) or not rv._out_streak(data, pid, priors[pid], week):
                continue
            back = next((w for w in range(week, data.last_week + 1) if (pid, w) in data.actual), None)
            found[NEVER if back is None else sum(_team_game(data, pid, w) for w in range(week, back))] += 1
    return found


REPORT_SEEDS = 20              # season_reports: news noise draws per absence


def season_reports(data: SeasonData, end_week: int) -> dict[int, list[float]]:
    """{games left // 4 (capped at 3): [season-ending reports, chart the real return was worth]} over every IR
    or PUP absence and REPORT_SEEDS news draws: the market price of an "out for the season" report
    (replay_chart_injury.SEASON_REPORT_KEEP = worth / reports)."""
    found: dict[int, list[float]] = defaultdict(lambda: [0, 0.0])
    for (pid, since), back in data.returns.items():
        tag = data.injuries[(pid, since)].tag
        if tag not in replay_news.SEASON_TAGS or data.players[pid].position not in SKILL:
            continue
        for week in range(since, min(back or end_week + 1, end_week + 1)):
            games = [w for w in range(week, end_week + 1) if _team_game(data, pid, w)]
            for seed in range(REPORT_SEEDS) if games else ():
                if replay_news.report(seed, pid, since, back, week, tag, data.last_week).back is not None:
                    continue
                cell = found[min(len(games) // 4, 3)]
                cell[0] += 1
                if back and back <= end_week:
                    cell[1] += 1 - replay_chart_injury.discount(sum(w < back for w in games), len(games))
    return found


def _session(db: Path):
    """A read-only in-memory copy of the database (replay_value_eval.read_only_copy): the check never writes."""
    from services.replay_value_eval import read_only_copy
    return read_only_copy(db)


def run(seasons: Sequence[int], weeks: Sequence[int], scoring: str, teams: int,
        lineups: Sequence[str], bench: int = 6, db: Path | None = None) -> dict:
    from services import replay_pool
    from services.replay_value_eval import DEFAULT_DB

    rows: dict[str, list[Row]] = defaultdict(list)
    counts: dict[str, Counter] = defaultdict(Counter)
    with _session(db or DEFAULT_DB) as session:
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
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="the database, read through a read-only copy")
    parser.add_argument("--season-reports", action="store_true",
                        help="measure what an 'out for the season' report was worth (SEASON_REPORT_KEEP)")
    parser.add_argument("--absences", action="store_true",
                        help="measure games missed by unreported absences (replay_chart_injury.NO_REPORT_ODDS)")
    parser.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"),
                        help="score two saved reports on their common player-weeks (nothing is rebuilt)")
    args = parser.parse_args(argv)
    if args.season_reports:
        from services import replay_pool
        totals: dict[int, list[float]] = defaultdict(lambda: [0, 0.0])
        with _session(args.db) as session:
            for season in parse_seasons(args.seasons):
                data = replay_pool.load_season_data(session, season, args.scoring)
                for bucket, (n, worth) in season_reports(data, settings_for(season, "regular", args.scoring, 12).end_week).items():
                    totals[bucket][0] += n
                    totals[bucket][1] += worth
        log.info("Season-ending reports, chart kept by games left // 4: %s",
                 {b: (n, round(w / n, 4)) for b, (n, w) in sorted(totals.items())})
        return
    if args.absences:
        from services import replay_pool
        found: Counter = Counter()
        with _session(args.db) as session:
            for season in parse_seasons(args.seasons):
                found.update(absences_unreported(replay_pool.load_season_data(session, season, args.scoring)))
        total = sum(found.values())
        log.info("Unreported absences, %d player-weeks: %s", total,
                 {missed: round(n / total, 4) for missed, n in sorted(found.items())})
        return
    if args.compare:
        before, after = (json.loads(Path(path).read_text()) for path in args.compare)
        for lineup, pair in compare(before, after).items():
            for name, result in pair.items():
                log.info("#### %s: %s", lineup, name)
                _log_report({lineup: result})
        return
    report = run(parse_seasons(args.seasons), [int(w) for w in args.weeks.split(",")], args.scoring, args.teams,
                 args.lineups.split(","), args.bench, args.db)
    _log_report(report)
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
