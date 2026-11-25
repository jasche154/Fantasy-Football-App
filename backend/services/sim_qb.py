"""Simulated seasons: who holds each team's quarterback job, measured the same way on real and simulated seasons.

A team's JOB HOLDER is its starting QB. Walking a season week by week (`walk`): the holder is the team's first
starter (its passing leader, `starters`); when another QB starts while the holder carries an injury tag, the
other is a fill-in and the holder keeps the job; when another QB starts while the holder is healthy (no tag),
the holder LOST THE JOB healthy if he also carries no tag the two weeks after and started fewer than half of the
team's games from then on (sim_next_year_calib's old season-level measure, made weekly). A holder who played a
later game for another team is gone (a trade or release), not benched. Two kinds of decision rows:

- "week": a healthy holder (no tag), not coming back from an absence, in a week from 2 to the season's last
  minus CHANGE_LAST_WEEKS (late-season rests are not benchings): did he lose the job that week?
- "return": the holder's first untagged week after missing games hurt, in the same weeks: did the fill-in keep
  the job?

Each row carries what was public before that week: his market standing (`market_ranks`: preseason ADP rank
among the season's QBs, at most UNRANKED), his form (`form`: points over projection in his starts so far, shrunk
by FORM_PRIOR_STARTS) and his team's record (`record`: (wins + 1) / (games + 2) - 0.5).

The model (sim_qb.json, `python -m services.sim_qb_fit --fit`; fitted on real 2019-2025 weeks, judged leave-season-
out): a weekly logistic hazard of a healthy job loss in market standing, form and record, and a logistic of
losing the job on return in market standing. SECURITY is the hazard's market part, -market x log(rank): the
higher, the safer. Starts over the last three seasons, production, draft pick, years since the draft, expert
ranks, the backup's record and a drafted rookie behind him were also tried and add nothing out of sample (or come
out with the wrong sign): the market already prices them. A holder whose security is at least `established`
(the security of the most secure real QB who lost a job healthy, 2019-2025) has a hazard of exactly zero while
healthy and always gets the job back. Every hazard is x QB_BENCH_SCALE (the user's choice: rarer than real life,
about half the real rate).

The engine side (sim_qb_jobs.py) plays these decisions week by week inside a simulated season.
"""
import math
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from services import sim_metrics
from services.replay_pool import SeasonData
from services.sim_constants import fitted_json

PATH = Path(__file__).with_name("sim_qb.json")
FIT = "python -m services.sim_qb_fit --fit"
QB_BENCH_SCALE = 0.5             # the user's choice: QBs lose their job about half as often as in real life
UNRANKED = 40                    # market rank of a QB the ADP list does not carry (lists carry about 35-45 QBs)
FORM_PRIOR_STARTS = 3.0          # pseudo-starts at zero points over projection in `form`
CHANGE_LAST_WEEKS = 2            # no benching in the season's last weeks (as sim_talent.CHANGE_LAST_WEEKS)
TOP_RANK = 12                    # sim_metrics: a "top-12" QB by market rank


@dataclass(frozen=True)
class Row:
    """One decision about a team's job holder (see the module docstring)."""
    team: str
    week: int
    holder: int
    kind: str                    # "week" | "return"
    lost: bool
    rank: int                    # his market rank
    form: float
    record: float
    starter: int                 # who started that week


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted QB job model (sim_qb.json)."""
    return fitted_json(PATH, FIT, ("hazard", "return", "established", "elevated", "prior"))


def market_ranks(data: SeasonData) -> dict[int, int]:
    """{QB: his preseason ADP rank among the season's QBs} (data's ADP list; absent: UNRANKED)."""
    order = sorted((p.adp, pid) for pid, p in data.players.items() if p.position == "QB" and p.adp)
    return {pid: min(rank, UNRANKED) for rank, (_, pid) in enumerate(order, start=1)}


def starters(data: SeasonData) -> dict[tuple[str, int], int]:
    """{(team, week): its starting QB}: the passing-yards leader of each played game (sim_metrics.qb_starts)."""
    out = {}
    for pid, weeks in sim_metrics.qb_starts(data).items():
        games = dict(data.team_by_week.get(pid, ()))
        for week in weeks:
            team = games.get(week) or data.team_at(pid, week)
            if team is not None:
                out[(team, week)] = pid
    return out


def form(data: SeasonData, pid: int, weeks: list[int]) -> float:
    """Points over projection in his starts `weeks` (played before the decision), shrunk to zero."""
    gaps = [data.actual[(pid, w)] - data.projected[(pid, w)] for w in weeks
            if (pid, w) in data.actual and (pid, w) in data.projected]
    return sum(gaps) / (len(gaps) + FORM_PRIOR_STARTS)


def record(data: SeasonData, team: str, week: int) -> float:
    """His team's (wins + 1) / (games + 2) - 0.5 over its games before `week` (data.scores)."""
    wins = games = 0.0
    for w in range(1, week):
        opponent = data.schedule.opponent(team, w)
        mine, theirs = data.scores.get((team, w)), data.scores.get((opponent, w)) if opponent else None
        if mine is not None and theirs is not None:
            games += 1
            wins += 1.0 if mine > theirs else 0.5 if mine == theirs else 0.0
    return (wins + 1) / (games + 2) - 0.5


def security(rank: int) -> float:
    """How safe his job is from his market rank: the hazard's market part, negated (higher = safer)."""
    return -stored()["hazard"]["market"] * math.log(max(1, rank))


def established(rank: int) -> bool:
    return security(rank) >= stored()["established"]


def _logistic(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def raw_hazard(rank: int, form_: float, record_: float) -> float:
    """The fitted real weekly chance a healthy holder loses the job (before QB_BENCH_SCALE and the established
    range)."""
    fit = stored()["hazard"]
    return _logistic(fit["intercept"] + fit["market"] * math.log(max(1, rank)) + fit["form"] * form_
                     + fit["record"] * record_)


def hazard(rank: int, form_: float, record_: float) -> float:
    """The simulated weekly chance a healthy holder loses the job: zero in the established range."""
    return 0.0 if established(rank) else QB_BENCH_SCALE * raw_hazard(rank, form_, record_)


def return_loss(rank: int) -> float:
    """The simulated chance a holder back from injury finds his fill-in keeping the job: zero in the established
    range, else the fitted real chance x QB_BENCH_SCALE."""
    if established(rank):
        return 0.0
    fit = stored()["return"]
    return QB_BENCH_SCALE * _logistic(fit["intercept"] + fit["market"] * math.log(max(1, rank)))


def start_chance(points: float) -> float:
    """The chance a QB whose first projections average `points` starts (the fitted `prior`; sim_world's starts
    weight for a QB with no prior World)."""
    fit = stored()["prior"]
    return _logistic(fit["intercept"] + fit["points"] * points)


def _gone(data: SeasonData, pid: int, team: str, week: int) -> bool:
    """He played a game for another team at `week` or later: traded or released, not benched."""
    return any(t != team and w >= week for w, t in data.team_by_week.get(pid, ()))


def _lost(data: SeasonData, starts: Mapping[int, int], pid: int, week: int, causal: bool) -> bool:
    """`starts`: {week: starter} of his team. Healthy at `week` and not starting: did he lose the job? With
    `causal` (a live season's holder), on what is known: yes unless he played in that game too or a tag of the
    next two weeks is known (he left the game hurt), whatever the later games."""
    if starts[week] == pid or (pid, week + 1) in data.injuries or (pid, week + 2) in data.injuries:
        return False
    if causal:
        return (pid, week) not in data.box
    rest = [w for w in starts if w >= week]
    return sum(1 for w in rest if starts[w] == pid) < len(rest) / 2


def walk(data: SeasonData, starts: Mapping[tuple[str, int], int] | None = None,
         causal: bool = False) -> tuple[list[Row], dict[str, tuple[int, bool]]]:
    """(decision rows, {team: (holder after the last played week, out: he missed that game hurt)}) of a season
    (module docstring). `starts`: {(team, week): starter}, else the passing leaders (`starters`); `causal`: a
    healthy holder who did not start lost the job (no look at later weeks), for a live season's holder."""
    starts = starters(data) if starts is None else starts
    ranks = market_ranks(data)
    by_team: dict[str, dict[int, int]] = defaultdict(dict)
    for (team, week), pid in starts.items():
        by_team[team][week] = pid
    rows, holders = [], {}
    for team in sorted(by_team):
        weeks = dict(sorted(by_team[team].items()))
        order = list(weeks)
        holder, out = weeks[order[0]], False
        for i, week in enumerate(order[1:], start=1):
            starter = weeks[week]
            if _gone(data, holder, team, week):
                holder, out = starter, False
                continue
            tagged = (holder, week) in data.injuries
            if starter != holder and tagged:
                out = True
                continue
            kind = "return" if out else "week"
            if not 2 <= week <= data.last_week - CHANGE_LAST_WEEKS or (tagged and kind == "week"):
                if not tagged and _lost(data, weeks, holder, week, causal):
                    holder = starter                  # a late-season change: no row
                out = out and starter != holder
                continue
            lost = _lost(data, weeks, holder, week, causal)
            mine = [w for w in order[:i] if weeks[w] == holder]
            rows.append(Row(team, week, holder, kind, lost, ranks.get(holder, UNRANKED), form(data, holder, mine),
                            record(data, team, week), starter))
            holder, out = (starter if lost else holder), False
        holders[team] = (holder, out)
    return rows, holders


def job_metrics(data: SeasonData, from_week: int = 1) -> dict[str, float]:
    """The realism check's season numbers from `walk` (decisions in weeks >= from_week): healthy job losses (a
    benching, or a fill-in keeping the job), those of a top-TOP_RANK QB by market rank, and the share of holders
    back from injury who start again."""
    rows = [r for r in walk(data)[0] if r.week >= from_week]
    lost = [r for r in rows if r.lost]
    back = [r for r in rows if r.kind == "return"]
    return {"QB benched healthy": float(sum(1 for r in lost if r.kind == "week")),
            "QB lost job on return": float(sum(1 for r in lost if r.kind == "return")),
            "QB top-12 lost job": float(sum(1 for r in lost if r.rank <= TOP_RANK)),
            "QB back after injury %": 100.0 * sum(1 for r in back if not r.lost) / len(back) if back else float("nan")}


def same_starters(sim: SeasonData, real: SeasonData, week: int) -> dict[str, float]:
    """The share of teams whose `week` starter in `sim` is the one who really started it."""
    mine, theirs = starters(sim), starters(real)
    teams = [team for (team, w) in theirs if w == week]
    same = sum(1 for team in teams if mine.get((team, week)) == theirs[(team, week)])
    return {f"QB week-{week} starter right %": 100.0 * same / len(teams) if teams else float("nan")}
