"""Simulated seasons: each team's QB job holder, decided week by week inside sim_season.simulate (the model and
its fit: services/sim_qb.py).

- `start`: the holders going into the first simulated week. A season simulated from week 1: each team's QB with
  the most starts weight in the belief (sim_next_year's August starter). A real season simulated from week N:
  sim_qb.walk over the real weeks before N, judged on the past only (`causal`: a healthy holder who did not
  start lost the job; one who missed the last game with a tag is OUT, a fill-in started), so the first simulated
  week's starter is that holder unless today's status (sim_setup.with_status_now) sits him; a holder no longer on
  the team (or none) falls back to the belief's most-starts QB.
- `decide`, before each simulated week's games, once its tags are known: a holder who sits (hurt, tagged out) is
  OUT and the next QB in line plays (sim_game's starter: the highest starts weight among the dressed). Back
  healthy, he gets the job again unless the fill-in who started the team's last game keeps it with
  sim_qb.return_loss (zero in the established range). A healthy holder without a tag, in weeks 2 to the last
  minus CHANGE_LAST_WEEKS, loses the job with sim_qb.hazard of his market rank, his form in his starts so far and
  his team's record (zero in the established range): the next QB in line (the truth's starts weight: a drafted
  rookie by his pick, sim_next_year) who does not sit becomes the holder. One draw per team and week from
  replay_state.rng(seed, "sim", "qb", week, team). Everything it reads is public before the week (results
  played, projections, tags, ADP); the truth only through `world`, for who is on the team and next in line.
- `apply`: the holder's starts weight above every teammate's, in the truth that plays the week and in the public
  belief that projects it (a benching is announced before the game).
- Rumours: each healthy holder outside the established range whose real hazard (sim_qb.raw_hazard) is at least
  sim_qb.json's `elevated` (the real mean outside that range) may get a false job-in-danger line, weighted by his
  chance to lose the job over the change weeks left (sim_talent.rumors); a benching gets the true line
  (a "bench" RoleChange, sim_market.buzz).
"""
from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from services import replay_state, sim_qb
from services.replay_pool import Injury, SeasonData
from services.sim_world import Player, RoleChange, World

HOLDER_LEAD = 1.0                # the holder's starts weight is his teammates' highest plus this


@dataclass
class Jobs:                      # mutable bookkeeping inside one simulate() call
    holders: dict[str, int]      # team -> its job holder
    out: dict[str, bool]         # team -> its holder missed the team's last game hurt
    ranks: Mapping[int, int]     # QB -> market rank (sim_qb.market_ranks of the season's ADP)


def _qbs(world: World, team: str) -> list[Player]:
    return sorted((p for p in world.roster(team) if p.position == "QB"), key=lambda p: (-p.starts, p.player_id))


def start(base: SeasonData, belief: World, from_week: int) -> Jobs:
    """The holders going into from_week (see the module docstring)."""
    real = any(week < from_week for _, week in base.actual)
    found = sim_qb.walk(base.as_of(from_week), causal=True)[1] if real else {}
    holders, out = {}, {}
    for team in sorted(belief.teams):
        qbs = _qbs(belief, team)
        pid, missed = found.get(team, (None, False))
        if pid not in {p.player_id for p in qbs}:
            if not qbs:
                continue
            pid, missed = qbs[0].player_id, False
        holders[team], out[team] = pid, missed
    return Jobs(holders, out, sim_qb.market_ranks(base))


def _last_starter(starts: Mapping[tuple[str, int], int], team: str, week: int) -> int | None:
    played = [w for (t, w) in starts if t == team and w < week]
    return starts[(team, max(played))] if played else None


def decide(jobs: Jobs, world: World, known: SeasonData, week: int, sitting: Collection[int],
           tags: Mapping[int, Injury], seed: int) -> tuple[list[RoleChange], dict[int, float]]:
    """(the week's benchings as "bench" RoleChanges of the old holder, {holder: his chance to lose the job over the
    change weeks left} for the rumours). Updates `jobs`. `known`: the season as known before `week`; `world`: the
    truth at `week`; `sitting`: who does not play it; `tags`: its tags."""
    starts = sim_qb.starters(known)
    last_change = known.last_week - sim_qb.CHANGE_LAST_WEEKS
    changes, rumors = [], {}
    for team in sorted(jobs.holders):
        holder, draw = jobs.holders[team], replay_state.rng(seed, "sim", "qb", week, team).random()
        rank = jobs.ranks.get(holder, sim_qb.UNRANKED)
        if holder in sitting:
            jobs.out[team] = True
            continue
        if not 2 <= week <= last_change:
            jobs.out[team] = False
            continue
        if jobs.out[team]:
            jobs.out[team] = False
            new = _last_starter(starts, team, week)
            on_team = {p.player_id for p in _qbs(world, team)}
            if new is None or new == holder or new not in on_team or new in sitting or draw >= sim_qb.return_loss(rank):
                continue
        else:
            if holder in tags:
                continue
            mine = [w for (t, w), pid in starts.items() if t == team and pid == holder]
            form, record = sim_qb.form(known, holder, mine), sim_qb.record(known, team, week)
            chance = sim_qb.hazard(rank, form, record)
            if chance > 0 and sim_qb.raw_hazard(rank, form, record) >= sim_qb.stored()["elevated"]:
                rumors[holder] = 1.0 - (1.0 - chance) ** (last_change - week + 1)
            new = next((p.player_id for p in _qbs(world, team) if p.player_id != holder and p.player_id not in sitting),
                       None)
            if new is None or draw >= chance:
                continue
        jobs.holders[team] = new
        changes.append(RoleChange(week, holder, kind="bench"))
    return changes, rumors


def apply(world: World, jobs: Jobs) -> World:
    """`world` with each holder's starts weight above every teammate's (he plays whenever he dresses)."""
    players = dict(world.players)
    for team, pid in jobs.holders.items():
        if pid in players and players[pid].team == team:
            top = max(p.starts for p in _qbs(world, team))
            players[pid] = replace(players[pid], starts=top + HOLDER_LEAD)
    return World(MappingProxyType(players), world.teams, world.changes)


def expected_benchings(jobs: Jobs, weeks: int) -> float:
    """The benchings a season is expected to make over `weeks` change weeks at neutral form and record (sizes the
    false buzz, sim_market.expected_hints)."""
    return sum(1.0 - (1.0 - sim_qb.hazard(jobs.ranks.get(pid, sim_qb.UNRANKED), 0.0, 0.0)) ** weeks
               for pid in jobs.holders.values())
