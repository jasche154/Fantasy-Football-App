"""Simulated seasons: fill weeks from_week..last_week of a SeasonData with simulated football.

`simulate(base, seed, from_week, prior)`: `base` holds real data for weeks < from_week (possibly none) and
the full schedule. Real weeks are copied through untouched; base rows for later weeks are dropped, except
the injury tags and expert ranks of from_week itself (known before its games: the snapshot) and its
projections, which say who exists for from_week's belief (a new season's depth players, sim_next_year) and are
then replaced by the simulated ones.

1. belief = sim_world.estimate(base, from_week, prior); truth = sim_talent.truth(belief, seed, ...).
2. Absences already running: a player hard-tagged at the snapshot with no game since sits from_week and
   sim_injuries.open_spell more games. At from_week 1 with no real games, sim_injuries.preseason.
3. Each week W, in order:
   - tags known before W: sitting players get sim_injuries.tag (byes included), a player's first game back
     may carry return_tag's Q, and healthy regulars (PHANTOM_MIN_SNAP) may be listed Q/D (phantom) and play;
   - the public belief estimate(data so far, W) gives W's projections (hard-tagged players out), expert ranks
     (when the week has none) and buzz notes (sim_market);
   - the truth at W plays every game (sim_game.play). Players on the field are hurt with
     sim_injuries.hazard (scaled down below FULL_EXPOSURE_SNAP); a hurt player plays exit_share of the game,
     then misses games_out team games (tagged from W + 1, as real tags are known before the week).
4. `returns` is recomputed from all tags and games (replay_pool.hard_tag_returns).

Deterministic for (base, seed, from_week, prior): every draw comes from replay_state.rng(seed, "sim", ...).
"""
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType

from services import replay_pool, replay_state, sim_injuries, sim_market, sim_talent
from services.replay_pool import Injury, SeasonData, hard_tag_returns
from services.sim_game import on_field, play
from services.sim_world import World, estimate, scoring_of

FULL_EXPOSURE_SNAP = 20.0        # a player who really plays (a recorded game) carries the full injury hazard
PHANTOM_MIN_SNAP = 50.0          # healthy regulars this involved may be listed Q/D (the fit counted relevant ones)
INJURY_POSITIONS = ("QB", "RB", "WR", "TE", "K")
UNKNOWN_BODY = "Undisclosed"


@dataclass
class _Absence:                  # mutable bookkeeping inside one simulate() call
    spell: sim_injuries.Spell
    left: int                    # team games still to miss
    missed: int = 0              # games missed so far


@dataclass
class _Tables:
    actual: dict
    projected: dict
    games: dict                  # pid -> [(week, team)]
    injuries: dict
    usage: dict
    box: dict
    expert_ranks: dict
    expert_age: dict
    notes: dict

    def season(self, base: SeasonData) -> SeasonData:
        team_by_week = {pid: tuple(sorted(games)) for pid, games in self.games.items() if games}
        return replace(base, known_before=None, actual=MappingProxyType(self.actual),
                       projected=MappingProxyType(self.projected), team_by_week=MappingProxyType(team_by_week),
                       injuries=MappingProxyType(self.injuries), usage=MappingProxyType(self.usage),
                       box=MappingProxyType(self.box), expert_ranks=MappingProxyType(self.expert_ranks),
                       expert_age=MappingProxyType(self.expert_age), notes=MappingProxyType(self.notes))


def _real(base: SeasonData, from_week: int) -> _Tables:
    before = lambda table: {k: v for k, v in table.items() if k[1] < from_week}   # noqa: E731
    games = {pid: [g for g in played if g[0] < from_week] or [g for g in played[:1]]
             for pid, played in base.team_by_week.items()}
    projected = {k: v for k, v in base.projected.items() if k[1] <= from_week}   # from_week's: replaced in _market
    return _Tables(actual=before(base.actual), projected=projected, games=games,
                   injuries={k: v for k, v in base.injuries.items() if k[1] <= from_week},
                   usage=before(base.usage), box=before(base.box),
                   expert_ranks={k: v for k, v in base.expert_ranks.items() if k[1] <= from_week},
                   expert_age={w: v for w, v in base.expert_age.items() if w <= from_week},
                   notes={k: v for k, v in base.notes.items() if k[1] <= from_week})


def _known(base: SeasonData, tables: _Tables, week: int) -> SeasonData:
    """What is known before `week`: an as_of copy, built without as_of's cache (one per week would pile up)."""
    return replay_pool._as_of.__wrapped__(tables.season(base), week)


def _streak(base: SeasonData, tagged: Mapping[int, Injury], pid: int, team: str,
            week: int) -> tuple[Injury, int] | None:
    """(hard tag, games missed counting this week) of an absence running at `week`, else None.
    `tagged`: his tags by week."""
    known = [w for w in tagged if w <= week]
    if not known:
        return None
    injury = tagged[max(known)]
    if injury.tag not in replay_pool.IR_TAGS or any((pid, w) in base.actual for w in range(max(known), week)):
        return None
    missed, w = 0, week
    while w >= 1 and (pid, w) not in base.actual and not (w in tagged and not tagged[w].hard):
        missed += 1 if base.schedule.opponent(team, w) else 0
        w -= 1
    return injury, max(1, missed)


def _absences(base: SeasonData, belief: World, seed: int, from_week: int) -> dict[int, _Absence]:
    dice = replay_state.rng(seed, "sim", "absences")
    real_games = any(week < from_week for _, week in base.actual)
    by_player: dict[int, dict[int, Injury]] = defaultdict(dict)
    for (pid, week), injury in base.injuries.items():
        by_player[pid][week] = injury
    out = {}
    for pid in sorted(belief.players):
        player = belief.players[pid]
        if player.position not in INJURY_POSITIONS:
            continue
        if from_week == 1 and not real_games:
            spell = sim_injuries.preseason(dice, player.position)
            if spell is not None:
                out[pid] = _Absence(spell, spell.games_out)
            continue
        running = _streak(base, by_player.get(pid, {}), pid, player.team, from_week)
        if running is not None:
            injury, missed = running
            left = sim_injuries.open_spell(dice, player.position, injury.tag, missed)
            spell = replace(sim_injuries.draw(dice, player.position), games_out=missed + left, exit_share=1.0,
                            body=injury.injury or UNKNOWN_BODY)
            out[pid] = _Absence(spell, left + 1, missed - 1)
    return out


def _tags(base: SeasonData, truth: World, absences: Mapping[int, _Absence], returning: Mapping[int, sim_injuries.Spell],
          week: int, from_week: int, dice) -> dict[int, Injury]:
    """The tags known before `week` (the snapshot's own at from_week win)."""
    tags = {pid: sim_injuries.tag(a.spell, a.missed) for pid, a in absences.items() if a.left > 0}
    for pid, spell in returning.items():
        back = sim_injuries.return_tag(spell)
        if back is not None and pid in truth.players and base.schedule.opponent(truth.players[pid].team, week):
            tags[pid] = back
    world = truth.at(week)
    for team in sorted(truth.teams):
        if not base.schedule.opponent(team, week):
            continue
        for pid, snap in sorted(on_field(world, team, set(tags)).items()):
            if snap >= PHANTOM_MIN_SNAP and pid not in tags:
                listed = sim_injuries.phantom(dice, truth.players[pid].position)
                if listed is not None:
                    tags[pid] = listed
    if week == from_week:
        tags.update({pid: i for (pid, w), i in base.injuries.items() if w == week})
    return tags


def _hurt(world: World, team: str, out: set[int], dice) -> dict[int, sim_injuries.Spell]:
    hurt = {}
    for pid, snap in sorted(on_field(world, team, out).items()):
        position = world.players[pid].position
        if position in INJURY_POSITIONS and dice.random() < sim_injuries.hazard(position) * min(1.0, snap / FULL_EXPOSURE_SNAP):
            hurt[pid] = sim_injuries.draw(dice, position)
    return hurt


def _market(tables: _Tables, base: SeasonData, belief: World, truth: World, seed: int, week: int, out: set[int],
            false_rate: float) -> None:
    scoring, last = scoring_of(base), base.last_week
    known = _known(base, tables, week)
    for key in [key for key in tables.projected if key[1] == week]:     # the snapshot's own (from_week only)
        del tables.projected[key]
    tables.projected.update({(pid, week): p for pid, p in sim_market.projections(
        belief, base.schedule, week, out, scoring).items()})
    for rank_week in ((0, week) if week == 1 else (week,)):
        if not any(w == rank_week for _, w in tables.expert_ranks):
            ids = known.players if rank_week else base.as_of(0).players
            tables.expert_ranks.update(sim_market.expert_ranks(
                belief, ids, rank_week, replay_state.rng(seed, "sim", "experts", rank_week), scoring, out,
                last - max(1, rank_week) + 1))
            tables.expert_age[rank_week] = sim_market.EXPERT_AGE["ros" if rank_week else "pre"]
    tables.notes.update(sim_market.buzz(truth.changes, belief, week, replay_state.rng(seed, "sim", "buzz", week),
                                        false_rate))


def simulate(base: SeasonData, seed: int, from_week: int, prior: World | None = None) -> SeasonData:
    """A new SeasonData with weeks from_week..base.last_week simulated (see the module docstring)."""
    if not 1 <= from_week <= base.last_week:
        raise ValueError(f"Week {from_week} is outside the season (1-{base.last_week}).")
    scoring, last = scoring_of(base), base.last_week
    tables = _real(base, from_week)
    belief = estimate(base, from_week, prior)
    truth = sim_talent.truth(belief, seed, from_week, last)
    absences = _absences(base, belief, seed, from_week)
    returning: dict[int, sim_injuries.Spell] = {}
    false_rate = sim_market.expected_hints(len(truth.teams), last - from_week + 1, sim_talent.settings())
    for week in range(from_week, last + 1):
        tags = _tags(base, truth, absences, returning, week, from_week, replay_state.rng(seed, "sim", "tags", week))
        tables.injuries.update({(pid, week): injury for pid, injury in tags.items()})
        public_out = {pid for pid, injury in tags.items() if injury.hard}
        sitting = public_out | {pid for pid, a in absences.items() if a.left > 0}
        _market(tables, base, estimate(_known(base, tables, week), week, prior), truth, seed, week, public_out,
                false_rate)
        world = truth.at(week)
        played_teams, fresh = set(), set()
        for game in sorted((g for g in base.schedule.games if g.week == week), key=lambda g: g.home_team):
            if game.home_team not in world.teams or game.away_team not in world.teams:
                continue
            dice = replay_state.rng(seed, "sim", "hurt", week, game.home_team)
            hurt = {**_hurt(world, game.home_team, sitting, dice), **_hurt(world, game.away_team, sitting, dice)}
            result = play(world, game.home_team, game.away_team, sitting,
                          replay_state.rng(seed, "sim", "game", week, game.home_team), scoring,
                          {pid: spell.exit_share for pid, spell in hurt.items()})
            _record(tables, world, result, week)
            played_teams |= {game.home_team, game.away_team}
            fresh |= set(hurt)
            for pid, spell in hurt.items():
                if spell.games_out > 0:
                    absences[pid] = _Absence(spell, spell.games_out)
                    returning.pop(pid, None)
                elif spell.questionable_back:
                    returning[pid] = spell
        _advance(absences, returning, truth, played_teams, fresh, week, tables)
    # an as_of fallback (a real game after the cut) that no simulated game replaced is dropped
    tables.games = {pid: [g for g in games if g[0] < from_week or (pid, g[0]) in tables.actual]
                    for pid, games in tables.games.items()}
    final = tables.season(base)
    return replace(final, returns=MappingProxyType(hard_tag_returns(final.injuries, final.actual, last)))


def _record(tables: _Tables, world: World, result, week: int) -> None:
    for pid, line in result.players.items():
        team = world.players[pid].team
        tables.actual[(pid, week)] = line.points
        games = tables.games.setdefault(pid, [])
        if games and games[0][0] >= week:          # the as_of fallback (a later real game): replaced by sim games
            games.clear()
        games.append((week, team))
        if line.usage is not None:
            tables.usage[(pid, week)] = line.usage
            tables.box[(pid, week)] = line.box


def _advance(absences: dict[int, _Absence], returning: dict, truth: World, played: set[str], fresh: set[int],
             week: int, tables: _Tables) -> None:
    """After the week: sitters whose team played miss one more game; a finished absence may bring a Q back."""
    for pid in [pid for pid in returning if (pid, week) in tables.actual and pid not in fresh]:
        del returning[pid]
    for pid, absence in list(absences.items()):
        if pid in fresh:
            continue                               # hurt this week: his absence starts with the next game
        if pid in truth.players and truth.players[pid].team in played:
            absence.left -= 1
            absence.missed += 1
        if absence.left <= 0:
            del absences[pid]
            if absence.spell.questionable_back:
                returning[pid] = absence.spell
