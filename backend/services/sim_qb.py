"""Simulated seasons: who holds each team's quarterback job, measured the same way on real and simulated seasons.

A team's JOB HOLDER is its starting QB. Walking a season week by week (`walk`): the holder is the team's first
starter (its passing-yards leader, `starters`, the one definition the fit, the engine and the metrics use); when
another QB starts while the holder carries an injury tag, the other is a fill-in and the holder keeps the job;
when another QB starts while the holder is healthy (no tag), the holder LOST THE JOB healthy if he also carries no
tag the two weeks after and started fewer than half of the team's games from then on. A holder who played a later
game for another team is gone (a trade or release), not benched. Two kinds of decision rows, in weeks 2 to the
season's last minus CHANGE_LAST_WEEKS (late-season rests are not benchings):

- "week": a healthy holder (no tag), not coming back from an absence: did he lose the job that week?
- "return": the holder's first untagged week after missing games hurt: did the fill-in keep the job?

Each row carries what was public before that week: his market standing (`market_ranks`: preseason ADP rank among
the season's QBs, at most UNRANKED), his form (`form`: points over projection in his starts so far, shrunk by
FORM_PRIOR_STARTS), his team's record (`record`: (wins + 1) / (games + 2) - 0.5) and the pick of the best drafted
rookie QB on his team (this season's draft, pick ROOKIE_PICKS or better, not the holder).

ESTABLISHED (`established_qbs`, the user's choice informed by the data: established starters never lose the job
healthy): his preseason QB rank this season is CURRENT_RANK or better on a price that ignores games he is expected
to miss (a real season: its real ADP, `market_ranks`; a season simulated whole: the per-game value of its
preseason belief, `preseason_ranks`, as its ADP is discounted for injuries carried into it), OR his real preseason QB ADP rank was PAST_RANK or better in one of the
PAST_SEASONS seasons before (read by sim_setup.qb_inputs). An established holder never loses the job while
healthy and always gets it back from a fill-in; nobody else is protected.

The model (sim_qb.json, `python -m services.sim_qb_fit --fit`; fitted on real 2019-2025 weeks, judged leave-season-
out), for holders who are not established:
- `hazard`: weekly logistic of a healthy job loss in log market rank, form and record (rows with no drafted rookie
  behind him), x QB_BENCH_SCALE (the user's choice: veterans benched for veterans about half as often as in real
  life);
- `rookie`: weekly logistic of a drafted rookie taking over, in log pick, record and form (rows with one behind
  him), NOT scaled (the user's choice: a high pick behind a stopgap veteran usually starts by mid-season, as in
  real life);
- `return`: logistic of the fill-in keeping the job on return, in log market rank, x QB_BENCH_SCALE.

The engine side (sim_qb_jobs.py) plays these decisions week by week inside a simulated season.
"""
import math
from collections import defaultdict
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from services import sim_metrics
from services.replay_pool import SeasonData
from services.sim_constants import fitted_json
from services.sim_world import World

PATH = Path(__file__).with_name("sim_qb.json")
FIT = "python -m services.sim_qb_fit --fit"
QB_BENCH_SCALE = 0.5             # the user's choice: veterans lose the job to veterans about half as often as in real life
CURRENT_RANK = 12                # the user's choice: a preseason QB rank this good (per-game price) is established
PAST_RANK = 8                    # the user's choice: ... or a real preseason QB ADP rank this good in ...
PAST_SEASONS = 2                 # ... one of this many seasons before
ROOKIE_PICKS = 64                # a drafted rookie QB this high (first two rounds) may take over (rookie model)
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
    rookie_pick: int | None = None   # the best drafted rookie QB behind him (pick), if any
    established: bool = False


@lru_cache(maxsize=1)
def stored() -> dict:
    """The fitted QB job model (sim_qb.json)."""
    return fitted_json(PATH, FIT, ("hazard", "rookie", "return", "elevated", "prior"))


def market_ranks(data: SeasonData) -> dict[int, int]:
    """{QB: his preseason ADP rank among the season's QBs} (data's ADP list; absent: UNRANKED)."""
    order = sorted((p.adp, pid) for pid, p in data.players.items() if p.position == "QB" and p.adp)
    return {pid: min(rank, UNRANKED) for rank, (_, pid) in enumerate(order, start=1)}


def preseason_ranks(belief: World) -> dict[int, int]:
    """{QB: his rank among QBs by the market's per-game value (sim_market.values, half PPR) in a preseason
    belief, nobody believed out}: a price that ignores the games an injury is expected to cost him."""
    from services import sim_market      # imported here: sim_market imports sim_game, which imports sim_world

    healthy = World(MappingProxyType({pid: replace(p, out_weeks=0) for pid, p in belief.players.items()}), belief.teams)
    return {pid: rank for pid, rank in sim_market.position_ranks(healthy).items() if belief.players[pid].position == "QB"}


def established_qbs(preseason: Mapping[int, int], past: Collection[int]) -> frozenset[int]:
    """The established QBs (module docstring): `preseason` {QB: preseason_ranks}, `past` the QBs whose real
    preseason ADP rank was PAST_RANK or better in one of the PAST_SEASONS seasons before."""
    return frozenset({pid for pid, rank in preseason.items() if rank <= CURRENT_RANK} | set(past))


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


def _logistic(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def raw_hazard(rank: int, form_: float, record_: float) -> float:
    """The fitted real weekly chance a healthy holder with no drafted rookie behind him loses the job."""
    fit = stored()["hazard"]
    return _logistic(fit["intercept"] + fit["market"] * math.log(max(1, rank)) + fit["form"] * form_
                     + fit["record"] * record_)


def hazard(rank: int, form_: float, record_: float) -> float:
    """The simulated weekly chance of a benching for a holder who is not established: x QB_BENCH_SCALE."""
    return QB_BENCH_SCALE * raw_hazard(rank, form_, record_)


def rookie_takeover(pick: int, form_: float, record_: float) -> float:
    """The weekly chance a drafted rookie (`pick`) takes the job from a holder who is not established: the
    fitted real chance, not scaled."""
    fit = stored()["rookie"]
    return _logistic(fit["intercept"] + fit["pick"] * math.log(max(1, pick)) + fit["record"] * record_
                     + fit["form"] * form_)


def return_loss(rank: int) -> float:
    """The chance a holder who is not established finds his fill-in keeping the job when he is back from injury:
    the fitted real chance x QB_BENCH_SCALE."""
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


def _rookie_behind(picks: Mapping[int, tuple[str, int]], team: str, holder: int) -> int | None:
    """The best pick (ROOKIE_PICKS or better) of a drafted rookie QB on `team` who is not the holder."""
    return min((pick for pid, (t, pick) in picks.items() if t == team and pid != holder and pick <= ROOKIE_PICKS),
               default=None)


def walk(data: SeasonData, starts: Mapping[tuple[str, int], int] | None = None, causal: bool = False,
         picks: Mapping[int, tuple[str, int]] | None = None,
         established: Collection[int] = ()) -> tuple[list[Row], dict[str, tuple[int, bool]]]:
    """(decision rows, {team: (holder after the last played week, out: he missed that game hurt)}) of a season
    (module docstring). `starts`: {(team, week): starter}, else `starters`; `causal`: a healthy holder who did
    not start, nor play in that game, lost the job (no look at later weeks), for a live season's holder; `picks`:
    {drafted rookie QB: (team, pick)} of this season's draft; `established`: established_qbs."""
    starts = starters(data) if starts is None else starts
    ranks, picks = market_ranks(data), picks or {}
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
                            record(data, team, week), starter, _rookie_behind(picks, team, holder),
                            holder in established))
            holder, out = (starter if lost else holder), False
        holders[team] = (holder, out)
    return rows, holders


def job_metrics(data: SeasonData, from_week: int = 1, established: Collection[int] = ()) -> dict[str, float]:
    """The realism check's season numbers from `walk` (decisions in weeks >= from_week): healthy job losses (a
    benching, or a fill-in keeping the job), those of a top-TOP_RANK QB by market rank and of an `established`
    one, and the share of holders back from injury who start again."""
    rows = [r for r in walk(data, established=established)[0] if r.week >= from_week]
    lost = [r for r in rows if r.lost]
    back = [r for r in rows if r.kind == "return"]
    return {"QB benched healthy": float(sum(1 for r in lost if r.kind == "week")),
            "QB lost job on return": float(sum(1 for r in lost if r.kind == "return")),
            "QB top-12 lost job": float(sum(1 for r in lost if r.rank <= TOP_RANK)),
            "QB established lost job": float(sum(1 for r in lost if r.established)),
            "QB back after injury %": 100.0 * sum(1 for r in back if not r.lost) / len(back) if back else float("nan")}


def same_starters(sim: SeasonData, real: SeasonData, week: int) -> dict[str, float]:
    """The share of teams whose `week` starter in `sim` is the one who really started it."""
    mine, theirs = starters(sim), starters(real)
    teams = [team for (team, w) in theirs if w == week]
    same = sum(1 for team in teams if mine.get((team, week)) == theirs[(team, week)])
    return {f"QB week-{week} starter right %": 100.0 * same / len(teams) if teams else float("nan")}
