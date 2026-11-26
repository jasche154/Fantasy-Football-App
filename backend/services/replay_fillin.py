"""Fill-in quarterbacks, point in time, the same on real and simulated seasons (the trade chart's and the realism
measure's one definition: services/sim_qb_fillins.py reads `history` too).

A team's REGULAR starter is its first starter (the passing-yards leader of a game, sim_qb.starters), or its top QB
by preseason ADP when he opens the season with a preseason IR/PUP/SUS/EXE tag; another QB who starts while the
regular carries an injury tag is FILLING IN; one who starts while the regular is healthy, on the team and did not
play has TAKEN the job (one who left a game early, untagged, keeps it); a regular now on another team hands it to
whoever starts. On the team's bye the week before's news stands.

`fill_ins(data, week)` (data = the season as of `week`): {QB: FillIn} of the QBs whose chart prices them as fill-ins:
- "out": the regular starter carries a hard tag this week: the QB filling in (the last game's starter if he
  was filling in, else the team's best-projected other QB this week);
- "back": the regular starter is back (no hard tag) and the last game's starter was filling in for him (no
  probation: his regular is back);
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
from services.replay_pool import PRESEASON_TAGS, SeasonData
from services.sim_constants import fitted_json

PATH = Path(__file__).with_name("replay_fillin.json")
FIT = "python -m services.replay_fillin --fit"
BUCKETS = 5                      # starts so far: 0, 1, 2, 3, 4+
NEWS_SEEDS = range(20)           # the fit averages the noisy report over these seeds
STATES = ("keep", "named", "season", "unreported", "back", "taken")   # the fitted shares
MIN_GAMES = 20                   # a bucket with fewer real team games takes its state's pooled share
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


def _moved(data: SeasonData, pid: int, team: str, week: int) -> bool:
    """He is on another team at `week` (traded or released): no longer the team's regular, and not benched."""
    return data.team_at(pid, week) not in (team, None)


def _preseason_regular(data: SeasonData, team: str) -> int | None:
    """The team's top QB by preseason ADP when he carries a preseason IR/PUP/SUS/EXE tag in week 1 (he is the
    regular and the week-1 starter fills in for him), else None."""
    qbs = sorted((p.adp, pid) for pid, p in data.players.items()
                 if p.position == "QB" and p.adp is not None and data.team_at(pid, 1) == team)
    if not qbs:
        return None
    tag = data.injuries.get((qbs[0][1], 1))
    return qbs[0][1] if tag is not None and tag.hard and tag.tag in PRESEASON_TAGS else None


@lru_cache(maxsize=16)
def history(data: SeasonData) -> Mapping[str, tuple[tuple[int, int, int, bool, int, bool], ...]]:
    """`_history`, cached for the chart's as_of copies (a simulation's weekly copies use `_history`: no cache keeps
    them alive)."""
    return _history(data)


def _history(data: SeasonData) -> Mapping[str, tuple[tuple[int, int, int, bool, int, bool], ...]]:
    """{team: ((week, starter, regular before that game, filling in, regular after it, taken), ...)} over its
    played games, in order (module docstring). `taken`: the job moved in that game because the regular was
    healthy, on the team and did not play; a regular now on another team hands it on without a taking."""
    out = {}
    for team, weeks in _starters(data).items():
        regular, rows = _preseason_regular(data, team) or weeks[min(weeks)], []
        for week in sorted(weeks):
            starter = weeks[week]
            if starter != regular and _moved(data, regular, team, week):
                rows.append((week, starter, regular, False, starter, False))
                regular = starter
                continue
            filling = starter != regular and (regular, week) in data.injuries
            taken = not filling and starter != regular and (regular, week) not in data.box
            after = starter if taken else regular
            rows.append((week, starter, regular, filling, after, taken))
            regular = after
        out[team] = tuple(rows)
    return MappingProxyType(out)


def _streak(rows: Sequence[tuple]) -> tuple[int, int]:
    """(the last game's starter, his consecutive starts up to it)."""
    starter, count = rows[-1][1], 0
    for row in reversed(rows):
        if row[1] != starter:
            break
        count += 1
    return starter, count


@lru_cache(maxsize=64)
def fill_ins(data: SeasonData, week: int) -> Mapping[int, FillIn]:
    """{QB: FillIn} at `week` (module docstring); `data` is the season as of `week`."""
    return _fill_ins(data, week, history(data))


def _fill_ins(data: SeasonData, week: int, teams: Mapping[str, tuple]) -> Mapping[int, FillIn]:
    probation = int(stored()["probation"])
    found = {}
    for team, rows in teams.items():
        rows = [r for r in rows if r[0] < week]
        if not rows:
            continue
        starter, starts = _streak(rows)
        regular = rows[-1][4]
        if _moved(data, regular, team, week):              # traded since the team's last game
            if regular == starter:
                continue
            regular = starter
        news_week = _news_week(data, team, week)
        hurt = (tag := data.injuries.get((regular, news_week))) is not None and tag.hard
        if news_week < week:                                # a bye: he sat the last game hurt, nothing new since
            hurt = hurt or (rows[-1][3] and rows[-1][2] == regular)
        if hurt:                       # probation waits for the regular's news (`share`): is he expected back?
            pid = starter if starter != regular else _backup(data, team, regular, news_week)
            if pid is not None:
                found[pid] = FillIn(team, regular, starts if pid == starter else 0, "out")
        elif starter != regular and rows[-1][3]:
            found[starter] = FillIn(team, regular, starts, "back")       # the regular is back: no probation
        elif starter == regular and starts < probation:
            taking = [r for r in rows[-starts:] if r[5]]
            if taking and not _moved(data, taking[0][2], team, week):     # the one he took it from is gone: his QB
                found[starter] = FillIn(team, taking[0][2], starts, "taken")
    return MappingProxyType(found)


def _backup(data: SeasonData, team: str, regular: int, week: int) -> int | None:
    """The team's best-projected QB other than its regular in the latest week up to `week` with any (who starts
    while he is out; a simulated week has no projections yet when its fill-ins are read)."""
    best = [(w, points, pid) for (pid, w), points in data.projected.items()
            if w <= week and pid != regular and pid in data.players and data.players[pid].position == "QB"
            and data.team_at(pid, week) == team and points > 0]
    return max(best)[2] if best else None


def without_news(data: SeasonData, week: int) -> frozenset[int]:
    """The fill-ins at `week` where no news report is drawn (a simulated season while it is built): an "out"
    fill-in past `probation` is the team's QB when his regular's tag is one only a season-ending report carries
    (replay_news.SEASON_TAGS), as `share` decides it on an out-for-the-season report."""
    probation = int(stored()["probation"])
    found = _fill_ins(data, week, _history(data))
    return frozenset(pid for pid, fill in found.items()
                     if fill.state != "out" or fill.starts < probation
                     or getattr(_regular_tag(data, fill, week), "tag", None) not in replay_news.SEASON_TAGS)


def _news_week(data: SeasonData, team: str, week: int) -> int:
    """The week whose tags and news stand at `week`: the week before on the team's bye (no tag is filed then)."""
    return week if data.schedule.opponent(team, week) else week - 1


def _regular_tag(data: SeasonData, fill: FillIn, week: int):
    return data.injuries.get((fill.regular, _news_week(data, fill.team, week)))


def regular_news(data: SeasonData, fill: FillIn, week: int, seed: int) -> "replay_news.Report | None":
    """The regular starter's report before `week` (replay_news.report, the one his injured chart reads; on the
    team's bye, the week before's streak), or None when he has no open hard-tag streak."""
    streak = replay_news.open_streak(data, fill.regular, _news_week(data, fill.team, week))
    return replay_news.report(seed, fill.regular, *streak[:2], week, streak[2], data.last_week) if streak else None


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
            for seed in NEWS_SEEDS if fill.state == "out" else (0,):
                report = regular_news(known, fill, week, seed)
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
                                            for s in STATES}
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
        weight = len(NEWS_SEEDS) if state in ("keep", "named", "season", "unreported") else 1   # one case per news seed
        shares[state] = [round(s / g, 3) if g >= MIN_GAMES * weight else round(pooled, 3) for s, g in buckets]
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
