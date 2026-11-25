"""Simulated seasons: fill weeks from_week..last_week of a SeasonData with simulated football.

`simulate(base, seed, from_week, prior)`: `base` holds real data for weeks < from_week (possibly none) and
the full schedule. Real weeks are copied through untouched; base rows for later weeks are dropped, except
from_week's own rows when base carries them: its injury tags and expert ranks are kept (known before its games:
the snapshot) and its projections, which say who exists for from_week's belief (a new season's depth players,
sim_next_year), are replaced by the simulated ones (a player only they admit to SeasonData.as_of keeps
MIN_PROJECTION, so a backup QB stays in view before his first game). A live league's base (sim_setup.build) is
real only through from_week - 1, so it carries none: from_week's tags and expert ranks are simulated like any
later week's, from what is known through from_week - 1 (a half-published mid-week injury report is never read).

1. belief = sim_world.estimate(base, from_week, prior); truth = sim_talent.truth(belief, seed, ...).
2. Absences already running: a player hard-tagged at the snapshot with no game since (results before from_week
   only) misses from_week's game, none over a bye, and sim_injuries.open_spell more games. At from_week 1 with
   no real games, sim_injuries.preseason.
3. Each week W, in order:
   - tags known before W: sitting players get sim_injuries.tag (byes included), a player's first game back
     may carry return_tag's Q, and healthy regulars (PHANTOM_MIN_SNAP in the latest public belief, never the
     truth) may be listed Q/D (phantom) and play;
   - the public belief estimate(data so far, W) gives W's projections (hard-tagged players out), expert ranks
     (when the week has none; the preseason ones, week 0, follow base's ADP: sim_market.expert_ranks) and buzz
     notes (sim_market; false ones only in the weeks a true one can land, sim_talent.change_weeks);
   - the truth at W plays every game (sim_game.play). Players on the field are hurt with
     sim_injuries.hazard (scaled down below FULL_EXPOSURE_SNAP); a hurt player plays exit_share of the game,
     then misses games_out team games (tagged from W + 1, as real tags are known before the week).
4. `returns` is recomputed from all tags and games (replay_pool.hard_tag_returns).

Deterministic for (base, seed, from_week, prior): every draw comes from replay_state.rng(seed, "sim", ...).
"""
from collections import defaultdict
from collections.abc import Collection, Mapping
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
    """(hard tag, team games missed BEFORE `week`) of an absence running at `week`, else None. `tagged`: his
    tags by week. Only results before `week` are read: `week` itself is not played yet, whatever base holds."""
    known = [w for w in tagged if w <= week]
    if not known:
        return None
    injury = tagged[max(known)]
    if injury.tag not in replay_pool.IR_TAGS or any((pid, w) in base.actual for w in range(max(known), week)):
        return None
    missed, w = 0, week - 1
    while w >= 1 and (pid, w) not in base.actual and not (w in tagged and not tagged[w].hard):
        missed += 1 if base.schedule.opponent(team, w) else 0
        w -= 1
    return injury, missed


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
            injury, before = running
            # open_spell's "this game": from_week's, or over a bye his last missed one (the next if none yet)
            now = 1 if base.schedule.opponent(player.team, from_week) or not before else 0
            left = sim_injuries.open_spell(dice, player.position, injury.tag, before + now)
            spell = replace(sim_injuries.draw(dice, player.position), games_out=before + now + left, exit_share=1.0,
                            body=injury.injury or UNKNOWN_BODY)
            out[pid] = _Absence(spell, left + now, before)
    return out


def _tags(base: SeasonData, public: World, absences: Mapping[int, _Absence], returning: Mapping[int, sim_injuries.Spell],
          week: int, from_week: int, dice) -> dict[int, Injury]:
    """The tags known before `week` (the snapshot's own at from_week win). Phantom candidates and their draw order
    come from `public`, a belief from before the week, never the truth."""
    tags = {pid: sim_injuries.tag(a.spell, a.missed) for pid, a in absences.items() if a.left > 0}
    for pid, spell in returning.items():
        back = sim_injuries.return_tag(spell)
        if back is not None and pid in public.players and base.schedule.opponent(public.players[pid].team, week):
            tags[pid] = back
    for team in sorted(public.teams):
        if not base.schedule.opponent(team, week):
            continue
        for pid, snap in sorted(on_field(public, team, set(tags)).items()):
            if snap >= PHANTOM_MIN_SNAP and pid not in tags:
                listed = sim_injuries.phantom(dice, public.players[pid].position)
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
            false_rate: float, ranks: Mapping[int, int]) -> None:
    scoring, last = scoring_of(base), base.last_week
    known = _known(base, tables, week)
    snapshot = sorted(pid for pid, w in tables.projected if w == week)      # the snapshot's own (from_week only)
    for pid in snapshot:
        del tables.projected[(pid, week)]
    fresh = sim_market.projections(belief, base.schedule, week, out, scoring)
    tables.projected.update({(pid, week): p for pid, p in fresh.items()})
    if snapshot:
        # a player the snapshot alone admits (a backup QB, a WR6: no ADP, no game, no earlier projection) keeps a
        # token one, or as_of would drop him until he plays (SeasonData.as_of admits by ADP, projection or game)
        admitted = {pid for pid, w in (*tables.projected, *tables.actual) if w < week}
        admitted |= {pid for pid, player in base.players.items() if player.adp is not None}
        tables.projected.update({(pid, week): sim_market.MIN_PROJECTION for pid in snapshot
                                 if pid not in fresh and pid not in admitted})
    for rank_week in ((0, week) if week == 1 else (week,)):
        if not any(w == rank_week for _, w in tables.expert_ranks):
            ids = known.players if rank_week else base.as_of(0).players
            # preseason experts and the preseason market price players alike: the experts follow base's ADP
            market = {} if rank_week else {pid: p.adp for pid, p in base.players.items() if p.adp is not None}
            tables.expert_ranks.update(sim_market.expert_ranks(
                belief, ids, rank_week, replay_state.rng(seed, "sim", "experts", rank_week), scoring,
                last - max(1, rank_week) + 1, market))
            tables.expert_age[rank_week] = sim_market.EXPERT_AGE["ros" if rank_week else "pre"]
    tables.notes.update(sim_market.buzz(truth.changes, belief, week, replay_state.rng(seed, "sim", "buzz", week),
                                        false_rate, out, sim_talent.rumors(belief, ranks) if false_rate else None))


def simulate(base: SeasonData, seed: int, from_week: int, prior: World | None = None,
             rookies: Collection[int] = ()) -> SeasonData:
    """A new SeasonData with weeks from_week..base.last_week simulated (see the module docstring); `rookies`:
    the players in their first season (sim_talent.growth)."""
    if not 1 <= from_week <= base.last_week:
        raise ValueError(f"Week {from_week} is outside the season (1-{base.last_week}).")
    scoring, last = scoring_of(base), base.last_week
    tables = _real(base, from_week)
    belief = estimate(base, from_week, prior)
    truth = sim_talent.truth(belief, seed, from_week, last, rookies)
    absences = _absences(base, belief, seed, from_week)
    returning: dict[int, sim_injuries.Spell] = {}
    buzz_weeks = sim_talent.change_weeks(from_week, last)          # false buzz only where true buzz can land
    ranks = sim_talent.public_ranks(belief)                        # the public's ranks the rumours lean on
    false_rate = sim_market.expected_hints(sim_talent.expected_changes(belief, ranks, from_week, last),
                                           buzz_weeks[1] - buzz_weeks[0] + 1)
    public = belief                                  # the latest public belief (last week's, for this week's tags)
    for week in range(from_week, last + 1):
        tags = _tags(base, public, absences, returning, week, from_week, replay_state.rng(seed, "sim", "tags", week))
        tables.injuries.update({(pid, week): injury for pid, injury in tags.items()})
        public_out = {pid for pid, injury in tags.items() if injury.hard}
        sitting = public_out | {pid for pid, a in absences.items() if a.left > 0}
        public = estimate(_known(base, tables, week), week, prior)
        _market(tables, base, public, truth, seed, week, public_out,
                false_rate if buzz_weeks[0] <= week <= buzz_weeks[1] else 0.0, ranks)
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
        _advance(absences, returning, truth, played_teams, fresh)
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


def _advance(absences: dict[int, _Absence], returning: dict, truth: World, played: set[str], fresh: set[int]) -> None:
    """After the week: sitters whose team played miss one more game; a finished absence may bring a Q back. A
    returning player's Q was for his team's game this week, played or not (an inactive one records no line)."""
    def team_played(pid: int) -> bool:
        return pid in truth.players and truth.players[pid].team in played

    for pid in [pid for pid in returning if team_played(pid) and pid not in fresh]:
        del returning[pid]
    for pid, absence in list(absences.items()):
        if pid in fresh:
            continue                               # hurt this week: his absence starts with the next game
        if team_played(pid):
            absence.left -= 1
            absence.missed += 1
        if absence.left <= 0:
            del absences[pid]
            if absence.spell.questionable_back:
                returning[pid] = absence.spell
