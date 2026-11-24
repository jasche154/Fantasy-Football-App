"""sim_season: a simulated rest-of-season (and whole season) that the Replay engine plays like a real one."""
from collections import defaultdict
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_game as rg, replay_pool, replay_state, sim_season, sim_talent, sim_world
from tests import replay_fakes as fakes

FROM = 8
SEED = 11


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def simulated(data):
    return sim_season.simulate(data, SEED, FROM)


def _rows(season, week_test):
    return {name: {k: v for k, v in getattr(season, name).items() if week_test(k[1])}
            for name in ("actual", "projected", "box", "usage", "injuries", "expert_ranks", "notes")}


def test_real_weeks_are_copied_through_untouched(data, simulated):
    assert _rows(simulated, lambda w: w < FROM) == _rows(data, lambda w: w < FROM)
    def real(season):
        return {pid: before for pid, games in season.team_by_week.items()
                if (before := tuple(g for g in games if g[0] < FROM))}
    assert real(simulated) == real(data)
    assert simulated.players == data.players and simulated.schedule == data.schedule


def test_every_simulated_week_is_filled(data, simulated):
    for week in range(FROM, data.last_week + 1):
        playing = {t for g in data.schedule.games if g.week == week for t in (g.home_team, g.away_team)}
        dst = {pid for (pid, w) in simulated.actual if w == week and simulated.players[pid].position == "DST"}
        assert len(dst) == len(playing)
        assert any(w == week for _, w in simulated.projected)
        assert any(w == week for _, w in simulated.expert_ranks) and week in simulated.expert_age
        skill = [k for k in simulated.actual if k[1] == week and simulated.players[k[0]].position in replay_pool.SKILL]
        assert skill and all(k in simulated.box and k in simulated.usage for k in skill)
        assert all(float(v).is_integer() for k, v in simulated.actual.items() if k[1] == week
                   and simulated.players[k[0]].position in ("K", "DST"))
    teams = {(pid, w): t for pid, games in simulated.team_by_week.items() for w, t in games}
    assert all(key in teams for key in simulated.actual if key[1] >= FROM)


def test_box_lines_are_whole_numbers_and_points_match_them(simulated):
    from services.sim_game import _skill_points
    half = sim_world.scoring_of(simulated)
    for key, line in simulated.box.items():
        if key[1] >= FROM:
            assert all(float(v).is_integer() for v in line.__dict__.values())
            assert simulated.actual[key] - _skill_points(line, 0, simulated.players[key[0]].position, half) <= 0.001


def test_nobody_tagged_out_plays_and_returns_match_the_tags(simulated):
    hard = [k for k, injury in simulated.injuries.items() if injury.hard and k[1] >= FROM]
    assert hard and not [k for k in hard if k in simulated.actual]
    assert all(i.tag in replay_pool.TAGS and (i.practice is None or i.practice in replay_pool.PRACTICE)
               for i in simulated.injuries.values())
    assert dict(simulated.returns) == replay_pool.hard_tag_returns(simulated.injuries, simulated.actual, simulated.last_week)


def test_injured_players_miss_games_and_new_injuries_happen(data, simulated):
    fresh = {k for k, i in simulated.injuries.items() if i.hard and k[1] > FROM and (k[0], k[1] - 1) in simulated.actual}
    assert len(fresh) >= 5                                   # hurt in a game, tagged out the next week


def test_same_seed_same_season_other_seed_other_season(data, simulated):
    again = sim_season.simulate(data, SEED, FROM)
    assert _rows(again, lambda w: True) == _rows(simulated, lambda w: True)
    assert again.team_by_week == simulated.team_by_week and again.returns == simulated.returns
    other = sim_season.simulate(data, SEED + 1, FROM)
    assert _rows(other, lambda w: w >= FROM)["actual"] != _rows(simulated, lambda w: w >= FROM)["actual"]


def test_what_the_public_sees_before_a_week_does_not_depend_on_that_week_or_later(data, monkeypatch, simulated):
    """Re-roll every game and injury from week W on: everything known before W stays the same."""
    week = FROM + 3
    original = replay_state.rng

    def rerolled(seed, *stream):
        if stream[:2] in (("sim", "game"), ("sim", "hurt")) and stream[2] >= week:
            return original(seed + 1000, *stream)
        return original(seed, *stream)
    monkeypatch.setattr(replay_state, "rng", rerolled)
    changed = sim_season.simulate(data, SEED, FROM)
    known = lambda s, w: replay_pool._as_of.__wrapped__(s, w)   # noqa: E731
    before, after = known(simulated, week), known(changed, week)
    for name in ("actual", "projected", "box", "usage", "injuries", "expert_ranks", "notes", "expert_age"):
        assert getattr(before, name) == getattr(after, name), name
    assert _rows(changed, lambda w: w >= week)["actual"] != _rows(simulated, lambda w: w >= week)["actual"]


PUBLIC = ("actual", "projected", "box", "usage", "injuries", "expert_ranks", "notes", "expert_age")


def _rerolled(monkeypatch, streams) -> None:
    original = replay_state.rng

    def rerolled(seed, *stream):
        return original(seed + 1000, *stream) if stream[:2] in streams else original(seed, *stream)
    monkeypatch.setattr(replay_state, "rng", rerolled)


@pytest.mark.parametrize("from_week", [1, FROM])
def test_the_hidden_truth_reaches_the_public_only_through_results(data, monkeypatch, from_week):
    """Re-roll only the truth (talent levels and dated role changes): nothing public before the first simulated
    week's games may move (injury tags, projections, expert ranks, notes, ADP), while the games do."""
    base = data if from_week > 1 else _future(data)
    prior = None if from_week > 1 else sim_world.estimate(data, data.last_week + 1)
    first = sim_season.simulate(base, SEED, from_week, prior)
    _rerolled(monkeypatch, (("sim", "talent"), ("sim", "changes"), ("sim", "offseason"), ("sim", "teams")))
    changed = sim_season.simulate(base, SEED, from_week, prior)
    before, after = (replay_pool._as_of.__wrapped__(s, from_week) for s in (first, changed))
    for name in PUBLIC:
        assert getattr(before, name) == getattr(after, name), name
    assert {p: x.adp for p, x in before.players.items()} == {p: x.adp for p, x in after.players.items()}
    assert _rows(changed, lambda w: w >= from_week)["actual"] != _rows(first, lambda w: w >= from_week)["actual"]


def _future(data):
    empty = MappingProxyType({})
    return replace(data, actual=empty, projected=empty, team_by_week=empty, injuries=empty, usage=empty, box=empty,
                   expert_ranks=empty, expert_age=empty, returns=empty, notes=empty,
                   players=MappingProxyType({p: pl for p, pl in data.players.items() if pl.adp}))


def test_a_whole_future_season_from_a_prior(data):
    empty = MappingProxyType({})
    prior = sim_world.estimate(data, data.last_week + 1)
    future = replace(data, actual=empty, projected=empty, team_by_week=empty, injuries=empty, usage=empty, box=empty,
                     expert_ranks=empty, expert_age=empty, returns=empty, notes=empty,
                     players=MappingProxyType({p: pl for p, pl in data.players.items() if pl.adp}))
    season = sim_season.simulate(future, 3, 1, prior)
    assert {w for _, w in season.actual} == set(range(1, data.last_week + 1))
    assert any(w == 0 for _, w in season.expert_ranks) and season.expert_age[0] == 6
    preseason = [k for k, i in season.injuries.items() if k[1] == 1 and i.hard]
    assert all(k not in season.actual for k in preseason)


def test_the_replay_engine_plays_a_simulated_season_to_a_champion(simulated):
    settings = fakes.fake_settings()
    state = rg.auto_draft(rg.create_game(settings, simulated, 5), settings, simulated, 5)
    while state.phase != "complete":
        step = rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY
        state = rg.advance(state, settings, simulated, 5, step)
    assert state.champion in {team.id for team in state.teams}
    assert len(state.results) == settings.end_week
    scores = defaultdict(float)
    for result in state.results:
        for team, score in result.scores.items():
            scores[team] += score
    assert all(score > 0 for score in scores.values())


def test_a_league_that_jumps_in_drafts_on_a_board_whose_order_agrees_with_its_adp(data):
    """The draft room's default order (pre_rank: the week's expert snapshot, which the simulation draws) and the
    market ADP come from one belief, injured starters included."""
    from services import replay_chart, sim_metrics, sim_setup

    known = data.as_of(FROM)                        # as the live season loads: nothing from week FROM on
    base = replace(known, schedule=data.schedule, known_before=None, expert_ranks=MappingProxyType(
        {k: v for k, v in known.expert_ranks.items() if k[1] < FROM}))
    settings = replay_state.Settings(season=data.season, start_week=FROM, sim_from=FROM)
    drafted = sim_setup._market_adp(sim_season.simulate(base, SEED, FROM), settings)
    ranks = replay_chart.pre_ranks(replay_chart.pre_picks(drafted, replay_state.draft_week(settings)))
    top = sorted(ranks, key=ranks.get)[:60]
    adp = [drafted.players[pid].adp or 1000.0 for pid in top]
    assert sim_metrics.spearman([ranks[pid] for pid in top], adp) > 0.85
    assert max(adp[:12]) <= 48                      # nobody the market takes past round 4 is a first-rounder


def _live_base(data):
    """The live season as loaded mid-season: nothing from week FROM on but that week's snapshot, full schedule."""
    known = data.as_of(FROM)
    return replace(known, schedule=data.schedule, known_before=None, expert_ranks=MappingProxyType(
        {k: v for k, v in known.expert_ranks.items() if k[1] < FROM}))


def test_a_backup_seen_only_in_the_snapshot_stays_in_view_mid_season(data):
    """A backup with no ADP and no game whose first projection is the snapshot's (week FROM) must not vanish
    when the simulation replaces that week's projections and gives him none."""
    base = _live_base(data)
    backups = sorted(pid for pid, p in base.players.items() if p.position in ("QB", "WR") and p.adp is None)[:4]
    assert backups
    unseen = lambda table: MappingProxyType({k: v for k, v in table.items() if k[0] not in backups})  # noqa: E731
    base = replace(base, actual=unseen(base.actual), box=unseen(base.box), usage=unseen(base.usage),
                   team_by_week=MappingProxyType({p: g for p, g in base.team_by_week.items() if p not in backups}),
                   projected=MappingProxyType({**unseen(base.projected), **{(pid, FROM): 0.5 for pid in backups}}))
    assert set(backups) <= set(base.as_of(FROM).players)
    season = sim_season.simulate(base, SEED, FROM)
    for week in range(FROM, season.last_week + 1):
        assert set(backups) <= set(season.as_of(week).players), week


def _out_since(data, from_week: int, on_bye: bool, extra_game: bool = False):
    """(base, player): a WR hard-tagged O since from_week - 2 with no game since (his team on bye at from_week or
    not), the snapshot taken before from_week; `extra_game`: base (wrongly) holds his from_week game too."""
    teams = {t for t, _ in data.schedule.opponents}
    team = next(t for t in sorted(teams) if (data.schedule.opponent(t, from_week) is None) == on_bye
                and all(data.schedule.opponent(t, w) for w in (from_week - 2, from_week - 1)))
    pid = next(p for p in sorted(data.players) if data.players[p].position == "WR"
               and data.team_at(p, from_week) == team and (p, 1) in data.actual)
    base = _live_base(replace(data, schedule=data.schedule))
    gone = {(pid, w) for w in range(from_week - 2, data.last_week + 1)}
    keep = lambda table: {k: v for k, v in table.items() if k not in gone}   # noqa: E731
    actual = {**keep(base.actual), **({(pid, from_week): 9.0} if extra_game else {})}
    injuries = {**keep(base.injuries), **{(pid, w): replay_pool.Injury("O", "DNP", "Ankle")
                                          for w in range(from_week - 2, from_week + 1)}}
    return replace(base, actual=MappingProxyType(actual), box=MappingProxyType(keep(base.box)),
                   usage=MappingProxyType(keep(base.usage)), injuries=MappingProxyType(injuries)), pid


@pytest.mark.parametrize("on_bye", [False, True])
def test_an_open_absence_misses_the_drawn_games_and_no_more(data, monkeypatch, on_bye):
    """Out for two games at the snapshot, two more drawn: he misses from_week's game and two more, or over a bye
    only the two (the bye is no game to miss), and a from_week result in the base is never read as played."""
    monkeypatch.setattr(sim_season.sim_injuries, "open_spell", lambda dice, position, tag, weeks_out: 2)
    seen = []
    for extra in (False, True):
        base, pid = _out_since(data, FROM, on_bye, extra)
        absence = sim_season._absences(base, sim_world.estimate(base, FROM), SEED, FROM)[pid]
        seen.append((absence.left, absence.missed, absence.spell.games_out))
    assert seen[0] == seen[1]
    assert seen[0] == ((2, 2, 4) if on_bye else (3, 2, 5))


def test_buzz_runs_only_in_the_weeks_a_role_change_can_start(data, simulated):
    """False lines go where true ones can land (sim_talent.change_weeks), or every line outside it is false."""
    first, last = sim_talent.change_weeks(FROM, data.last_week)
    weeks = {w for _, w in simulated.notes if w >= FROM}
    assert weeks and weeks <= set(range(first, last + 1))


def test_a_returning_player_is_listed_q_for_his_first_game_back_only(data):
    """His Q is for his team's first game back, whether or not he records a line in it (an inactive depth player
    records none, and must not stay Q all season)."""
    world = sim_world.estimate(data, FROM)
    pid = next(p for p in sorted(world.players) if world.players[p].position == "WR")
    spell = sim_season.sim_injuries.Spell(0, 1.0, "Knee", True)
    returning = {pid: spell}
    sim_season._advance({}, returning, world, set(), set())             # his team on bye: still to come
    assert pid in returning
    sim_season._advance({}, returning, world, {world.players[pid].team}, set())
    assert not returning


DETERMINISM = """
import hashlib
from services import replay_blob, sim_market, sim_next_year as ny, sim_schedule as ss, sim_season, sim_world
from tests import replay_fakes as fakes

data = fakes.fake_season()
digest = lambda season: hashlib.md5(replay_blob.dumps(season)).hexdigest()   # noqa: E731
print(digest(sim_season.simulate(data, 11, 8)))
end = sim_world.estimate(data, data.last_week + 1)
belief, pool = ny.next_year(end, data.players, dict.fromkeys(end.players, 3), {}, {}, {})
schedule = ss.build(data.season + 1, ss.standings_now(data.schedule.games), data.season + 1)
base = ny.season_base(data.season + 1, belief, pool, schedule, data.scoring, data.rules, {}, "ppr",
                      sim_market.track_record(data))
print(digest(base), digest(sim_season.simulate(base, 5, 1, belief)))
"""


def test_a_season_is_built_the_same_in_every_process():
    """A league's season is frozen once, but a live or future season must not depend on str hash order (set and
    dict order of team codes differ per process): a mid-season simulation and a future build (offseason, generated
    schedule, market ADP, whole simulated season) give identical blobs under three hash seeds."""
    from tests.test_sim_schedule import hash_seed_outputs

    outputs = hash_seed_outputs(DETERMINISM)
    assert len(set(outputs)) == 1 and len(outputs[0].split()) == 3, outputs
