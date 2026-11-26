"""Fill-in quarterbacks, point in time, the same on real and simulated seasons (the trade chart's and the realism
measure's one definition: services/sim_qb_fillins.py reads `history` too).

A team's REGULAR starter is its first starter (the passing-yards leader of a game, sim_qb.starters); another QB
who starts while the regular carries an injury tag is FILLING IN; one who starts while the regular is healthy and
did not play has taken the job (the regular changes; one who left a game early, untagged, keeps it).

`fill_ins(data, week)` (data = the season as of `week`): {QB: FillIn} of the QBs whose chart prices them as fill-ins:
- "out": the regular starter carries a hard tag this week: the QB filling in (the last game's starter if he
  was filling in, else the team's best-projected other QB this week);
- "back": the regular starter is back (no hard tag) and the last game's starter was filling in for him;
- "taken": the QB who took the job, while his starts since he first started in this stint are fewer than the
  fitted `probation`.
`starts`: his consecutive starts for the team before `week`.

`share(fill, report, game_weeks)`: the share of his team's remaining games he is expected to start, fitted on
real fill-ins 2019-2025 (`python -m services.replay_fillin --fit`, replay_fillin.json) by starts so far (0, 1, 2,
3, 4+) and what the regular starter's news says (replay_news.report, the noisy point-in-time report the injured
chart reads): a named return week -> `named` (the share such fill-ins really started), never more than the games
before it + `keep` x the games from it; out for the season -> `season`; no report -> `unreported`; back -> `back`;
taken -> `taken`. Probation: from `probation` consecutive starts (fitted: the start from which real fill-ins who
kept the job had a median expert rank inside the top 24, 2020-2025) he is the team's QB, unless his regular is
expected back (a named return week): then the return still bounds him. The chart (replay_chart.with_news) prices a
fill-in at share x the value of his rank without the form bonus + (1 - share) x a backup's.

Pure apart from the fit: no database at valuation time.
"""
import argparse
import json
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import replay_news
from services.replay_pool import SeasonData
from services.sim_constants import fitted_json

PATH = Path(__file__).with_name("replay_fillin.json")
FIT = "python -m services.replay_fillin --fit"
BUCKETS = 5                      # starts so far: 0, 1, 2, 3, 4+
NEWS_SEEDS = range(20)           # the fit averages the noisy report over these seeds
STATES = ("named", "season", "unreported", "back", "taken")
log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FillIn:
    team: str
    regular: int                 # the regular starter he is filling in for (or took the job from)
    starts: int                  # his consecutive starts for the team before the week
    state: str                   # "out" | "back" | "taken"


@lru_cache(maxsize=1)
def stored() -> Mapping:
    return MappingProxyType(fitted_json(PATH, FIT, ("shares", "probation")))


def _starters(data: SeasonData) -> dict[str, dict[int, int]]:
    from services import sim_qb      # imported here: the engine's QB module is heavy and this one is read by the chart

    by_team: dict[str, dict[int, int]] = defaultdict(dict)
    for (team, week), pid in sim_qb.starters(data).items():
        by_team[team][week] = pid
    return by_team


@lru_cache(maxsize=64)
def history(data: SeasonData) -> Mapping[str, tuple[tuple[int, int, int, bool], ...]]:
    """{team: ((week, starter, regular before that game, filling in), ...)} over its played games, in order."""
    out = {}
    for team, weeks in _starters(data).items():
        order = sorted(weeks)
        regular, rows = weeks[order[0]], [(order[0], weeks[order[0]], weeks[order[0]], False)]
        for week in order[1:]:
            starter = weeks[week]
            filling = starter != regular and (regular, week) in data.injuries
            rows.append((week, starter, regular, filling))
            if not filling and starter != regular and (regular, week) not in data.box:
                regular = starter                          # a healthy regular who did not play: the job moved
        out[team] = tuple(rows)
    return MappingProxyType(out)


def _streak(rows: Sequence[tuple[int, int, int, bool]]) -> tuple[int, int]:
    """(the last game's starter, his consecutive starts up to it)."""
    starter, count = rows[-1][1], 0
    for _, pid, _, _ in reversed(rows):
        if pid != starter:
            break
        count += 1
    return starter, count


@lru_cache(maxsize=256)
def fill_ins(data: SeasonData, week: int) -> Mapping[int, FillIn]:
    """{QB: FillIn} at `week` (module docstring); `data` is the season as of `week`."""
    probation = int(stored()["probation"])
    found = {}
    for team, rows in history(data).items():
        rows = [r for r in rows if r[0] < week]
        if not rows:
            continue
        starter, starts = _streak(rows)
        last_week, _, regular, filling = rows[-1]
        if not filling and starter != regular:
            regular = starter                                  # he took it in that game (history moved it after)
        hurt = (tag := data.injuries.get((regular, week))) is not None and tag.hard
        if hurt:                       # probation waits for the regular's news (`share`): is he expected back?
            pid = starter if starter != regular else _backup(data, team, regular, week)
            if pid is not None:
                found[pid] = FillIn(team, regular, starts if pid == starter else 0, "out")
        elif starter != regular and filling and starts < probation:
            found[starter] = FillIn(team, regular, starts, "back")
        elif starter == regular and starts < probation and len(rows) > starts:
            before = rows[-starts - 1]
            if before[1] != starter and before[2] != starter:    # he took the job from the one who started before
                found[starter] = FillIn(team, before[2], starts, "taken")
    return MappingProxyType(found)


def _backup(data: SeasonData, team: str, regular: int, week: int) -> int | None:
    """The team's best-projected QB other than its regular this week (who starts while he is out)."""
    best = [(points, pid) for (pid, w), points in data.projected.items()
            if w == week and pid != regular and pid in data.players and data.players[pid].position == "QB"
            and data.team_at(pid, week) == team and points > 0]
    return max(best)[1] if best else None


def state_of(fill: FillIn, report: "replay_news.Report | None") -> str:
    """The fitted category: what the regular starter's news says, or back / taken."""
    if fill.state != "out":
        return fill.state
    if report is None:
        return "unreported"
    return "season" if report.back is None else "named"


def share(fill: FillIn, report: "replay_news.Report | None", game_weeks: Sequence[int]) -> float | None:
    """The share of his team's remaining games (`game_weeks`) he is expected to start (module docstring), or None
    when he is no longer a fill-in: `probation` starts with his regular not expected back (no named return)."""
    if not game_weeks:
        return 0.0
    shares, bucket = stored()["shares"], min(fill.starts, BUCKETS - 1)
    state = state_of(fill, report)
    if state != "named":
        if state in ("season", "unreported") and fill.starts >= int(stored()["probation"]):
            return None
        return float(shares[state][bucket])
    before = sum(w < report.back for w in game_weeks) / len(game_weeks)
    return min(float(shares["named"][bucket]), before + (1 - before) * float(shares["keep"][bucket]))


# ---------------- the fit ----------------

def _cases(data: SeasonData) -> list[tuple[str, int, float, float]]:
    """(category, starts bucket, his starts, the games counted) for every fill-in situation of a real season, the
    noisy report drawn with each of NEWS_SEEDS (a named return counts only the games from the reported week)."""
    starts_by_team = _starters(data)
    last = max(w for w in range(1, data.last_week + 1) if any(g.week == w for g in data.schedule.games))
    out = []
    for week in range(2, last + 1):
        known = data.as_of(week)
        for pid, fill in fill_ins(known, week).items():
            games = [w for w in range(week, last + 1) if data.schedule.opponent(fill.team, w) is not None]
            mine = [w for w in games if starts_by_team[fill.team].get(w) == pid]
            bucket = min(fill.starts, BUCKETS - 1)
            streak = replay_news.open_streak(known, fill.regular, week)
            for seed in NEWS_SEEDS if fill.state == "out" else (0,):
                report = replay_news.report(seed, fill.regular, *streak[:2], week, streak[2], data.last_week) \
                    if streak else None
                state = state_of(fill, report)
                if state == "named":
                    after = [w for w in games if w >= report.back]
                    out.append(("keep", bucket, sum(w in mine for w in after), len(after)))
                    out.append(("named", bucket, len(mine), len(games)))
                elif state != "season" or fill.starts < int(stored()["probation"]):
                    out.append((state, bucket, len(mine), len(games)))
    return out


def fit(seasons: Sequence[int], probation: int) -> dict:
    from database.db import SessionLocal
    from services import replay_pool
    from services.sim_eval import SCORING

    totals: dict[str, list[list[float]]] = {s: [[0.0, 0.0] for _ in range(BUCKETS)]
                                            for s in ("keep", "named", "season", "unreported", "back", "taken")}
    with SessionLocal() as session:
        for season in seasons:
            for state, bucket, started, games in _cases(replay_pool.load_season_data(session, season, SCORING)):
                totals[state][bucket][0] += started
                totals[state][bucket][1] += games
    shares = {}
    for state, buckets in totals.items():
        if not any(g for _, g in buckets):
            continue                                  # never seen (a hard tag with no report): "season" stands in
        pooled = sum(s for s, _ in buckets) / sum(g for _, g in buckets)
        shares[state] = [round(s / g, 3) if g >= 20 else round(pooled, 3) for s, g in buckets]   # thin buckets: pooled
    shares.setdefault("unreported", shares["season"])
    return {"seasons": list(seasons), "probation": probation, "shares": shares,
            "games": {state: [g for _, g in buckets] for state, buckets in totals.items()}}


def main(argv: list[str] | None = None) -> None:
    from services.replay_data import parse_seasons     # the CLI's only use of the importer

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fit", action="store_true", help="measure and write replay_fillin.json")
    parser.add_argument("--seasons", default="2019-2025")
    parser.add_argument("--probation", type=int, required=False, default=None)
    args = parser.parse_args(argv)
    if not args.fit:
        parser.print_help()
        return
    probation = args.probation if args.probation is not None else int(stored()["probation"])
    fitted = fit(parse_seasons(args.seasons), probation)
    PATH.write_text(json.dumps(fitted, indent=1) + "\n")
    stored.cache_clear()
    fill_ins.cache_clear()
    log.info("wrote %s: %s", PATH.name, json.dumps(fitted))


if __name__ == "__main__":
    main()
