"""Simulated seasons: each team's QB job holder, decided week by week inside sim_season.simulate (the model and
its fit: services/sim_qb.py).

- `QbInputs`: what the engine needs beyond the SeasonData, read from the database by sim_setup.qb_inputs: the
  QBs established by their real ADP of the seasons before (`past`) and this season's drafted rookie QBs with their
  picks. `established_of`: the season's established QBs (sim_qb.established_qbs on preseason ranks: never
  in-season form).
- `start`: the holders going into the first simulated week. On each team, an established QB is the holder (the
  real weeks' holder if he is one, else the one who started most recently, else the highest starts weight); he is
  OUT when he did not play the team's last real game, never benched. Otherwise, from week 1: the belief's
  most-starts QB (sim_next_year's August starter); from week N: sim_qb.walk over the real weeks before N, judged
  on the past only (`causal`: the last real starter, unless the holder sat with a tag, then the holder before
  him, OUT); a holder no longer on the team falls back to the belief's most-starts QB. Today's status
  (sim_setup.with_status_now) then sits whoever is hurt.
- `decide`, before each simulated week's games, once its tags are known, for each team with a game that week
  (a bye decides nothing, as the fit has played weeks only): a holder who sits (hurt, tagged out) is OUT and the
  next QB in line plays (sim_game's starter: the highest starts weight among the dressed). Back healthy, he gets
  the job again unless he is not established and the fill-in who started the team's last game keeps it
  (sim_qb.return_loss). A healthy, untagged holder who is not established, in weeks 2 to the last minus
  CHANGE_LAST_WEEKS, loses the job: to the best drafted rookie on the team (pick ROOKIE_PICKS or better, not
  sitting) with sim_qb.rookie_takeover, else to the next QB in line (the truth's starts weight) with
  sim_qb.hazard. One draw per team and week from replay_state.rng(seed, "sim", "qb", week, team). Everything it
  reads is public before the week (results, projections, tags, ADP, the draft); the truth only through `world`,
  for who is on the team and next in line.
- `apply`: the holder's starts weight above every teammate's, in the truth that plays the week and in the public
  belief that projects it (a benching is announced before the game).
- Rumours: each healthy holder outside the established range whose real weekly chance (unscaled) is at least
  sim_qb.json's `elevated` (the real mean outside that range) may get a false job-in-danger line, weighted by his
  chance to lose the job over the change weeks left (sim_talent.rumors); a benching gets the true line
  (a "bench" RoleChange, sim_market.buzz).
"""
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType

from services import replay_state, sim_qb
from services.replay_pool import Injury, SeasonData
from services.sim_world import Player, RoleChange, World

HOLDER_LEAD = 1.0                # the holder's starts weight is his teammates' highest plus this


@dataclass(frozen=True)
class QbInputs:
    """What the QB jobs need from the database (sim_setup.qb_inputs); the default reads nothing."""
    past: frozenset[int] = frozenset()                                  # real ADP rank PAST_RANK+ in the seasons before
    picks: Mapping[int, int] = field(default_factory=lambda: MappingProxyType({}))   # drafted rookie QB -> pick


@dataclass
class Jobs:                      # mutable bookkeeping inside one simulate() call
    holders: dict[str, int]      # team -> its job holder
    out: dict[str, bool]         # team -> its holder missed the team's last game hurt
    ranks: Mapping[int, int]     # QB -> market rank (sim_qb.market_ranks of the season's ADP)
    established: frozenset[int] = frozenset()
    picks: Mapping[int, int] = field(default_factory=dict)             # drafted rookie QB -> pick


def _qbs(world: World, team: str) -> list[Player]:
    return sorted((p for p in world.roster(team) if p.position == "QB"), key=lambda p: (-p.starts, p.player_id))


def established_of(base: SeasonData, belief: World | None, whole: bool, inputs: QbInputs) -> frozenset[int]:
    """The season's established QBs (sim_qb.established_qbs). The preseason rank: in a real season its real
    preseason ADP rank (sim_qb.market_ranks), as a real season's belief, with no prior, rates every starting
    passer alike; in a season simulated whole from a prior World (`whole`: a future season, whose ADP is
    discounted for injuries carried into it) the better of that and the per-game value rank of its preseason
    `belief` (sim_qb.preseason_ranks), so neither a discount nor the value's order drops a top-12 QB."""
    ranks = sim_qb.market_ranks(base)
    if whole:
        per_game = sim_qb.preseason_ranks(belief)
        ranks = {pid: min(ranks.get(pid, sim_qb.UNRANKED), per_game.get(pid, sim_qb.UNRANKED)) for pid in {*ranks, *per_game}}
    return sim_qb.established_qbs(ranks, inputs.past)


def start(base: SeasonData, belief: World, from_week: int, established: frozenset[int],
          inputs: QbInputs = QbInputs()) -> Jobs:
    """The holders going into from_week (see the module docstring)."""
    real = any(week < from_week for _, week in base.actual)
    known = base.as_of(from_week)
    found = sim_qb.walk(known, causal=True)[1] if real else {}
    starts = sim_qb.starters(known)
    last_start = {pid: week for (_, week), pid in sorted(starts.items(), key=lambda kv: kv[0][1])}
    holders, out = {}, {}
    for team in sorted(belief.teams):
        qbs = _qbs(belief, team)
        if not qbs:
            continue
        ids = {p.player_id for p in qbs}
        pid, missed = found.get(team, (None, False))
        secure = [p.player_id for p in qbs if p.player_id in established]
        if secure and pid not in secure:       # most recent starter first, then the most starts weight (qbs order)
            pid = max(secure, key=lambda q: last_start.get(q, 0))
            missed = real and pid != _last_starter(starts, team, from_week)
        elif pid not in ids:
            pid, missed = qbs[0].player_id, False
        holders[team], out[team] = pid, missed
    return Jobs(holders, out, sim_qb.market_ranks(base), established, dict(inputs.picks))


def _last_starter(starts: Mapping[tuple[str, int], int], team: str, week: int) -> int | None:
    played = [w for (t, w) in starts if t == team and w < week]
    return starts[(team, max(played))] if played else None


def _returns(jobs: Jobs, team: str, holder: int, world: World, starts, week: int, sitting, draw: float) -> int | None:
    """The fill-in who keeps the job from a holder back from injury, or None."""
    new = _last_starter(starts, team, week)
    on_team = {p.player_id for p in _qbs(world, team)}
    if (holder in jobs.established or new is None or new == holder or new not in on_team or new in sitting
            or draw >= sim_qb.return_loss(jobs.ranks.get(holder, sim_qb.UNRANKED))):
        return None
    return new


def _challenger(jobs: Jobs, world: World, team: str, holder: int, sitting: Collection[int], form: float,
                record: float) -> tuple[int | None, float, float]:
    """(who takes the job if the holder loses it, the simulated weekly chance, the real one): the best drafted
    rookie on the team who does not sit, with sim_qb.rookie_takeover (not scaled), else the next QB in line, with
    sim_qb.hazard."""
    others = [p.player_id for p in _qbs(world, team) if p.player_id != holder and p.player_id not in sitting]
    rookies = [pid for pid in others if jobs.picks.get(pid, sim_qb.ROOKIE_PICKS + 1) <= sim_qb.ROOKIE_PICKS]
    if rookies:
        rookie = min(rookies, key=lambda pid: (jobs.picks[pid], pid))
        chance = sim_qb.rookie_takeover(jobs.picks[rookie], form, record)
        return rookie, chance, chance
    raw = sim_qb.raw_hazard(jobs.ranks.get(holder, sim_qb.UNRANKED), form, record)
    return (others[0] if others else None), sim_qb.QB_BENCH_SCALE * raw, raw


def decide(jobs: Jobs, world: World, known: SeasonData, week: int, sitting: Collection[int],
           tags: Mapping[int, Injury], seed: int) -> tuple[list[RoleChange], dict[int, float]]:
    """(the week's benchings as "bench" RoleChanges of the old holder, {holder: his chance to lose the job over the
    change weeks left} for the rumours). Updates `jobs`. `known`: the season as known before `week`; `world`: the
    truth at `week`; `sitting`: who does not play it; `tags`: its tags."""
    starts = sim_qb.starters(known)
    last_change = known.last_week - sim_qb.CHANGE_LAST_WEEKS
    changes, rumors = [], {}
    for team in sorted(jobs.holders):
        if not known.schedule.opponent(team, week):
            continue                                           # a bye: nothing to decide
        holder, draw = jobs.holders[team], replay_state.rng(seed, "sim", "qb", week, team).random()
        if holder in sitting:
            jobs.out[team] = True
            continue
        if not 2 <= week <= last_change:
            jobs.out[team] = False
            continue
        if jobs.out[team]:
            jobs.out[team] = False
            new = _returns(jobs, team, holder, world, starts, week, sitting, draw)
        elif holder in tags or holder in jobs.established:
            continue
        else:
            mine = [w for (t, w), pid in starts.items() if t == team and pid == holder]
            new, chance, raw = _challenger(jobs, world, team, holder, sitting, sim_qb.form(known, holder, mine),
                                           sim_qb.record(known, team, week))
            if new is not None and raw >= sim_qb.stored()["elevated"]:
                rumors[holder] = 1.0 - (1.0 - chance) ** (last_change - week + 1)
            new = new if draw < chance else None
        if new is None:
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
    """The benchings a season is expected to make over `weeks` change weeks at neutral form and record, for the
    holders who are not established (sizes the false buzz, sim_market.expected_hints)."""
    return sum(1.0 - (1.0 - sim_qb.hazard(jobs.ranks.get(pid, sim_qb.UNRANKED), 0.0, 0.0)) ** weeks
               for pid in jobs.holders.values() if pid not in jobs.established)
