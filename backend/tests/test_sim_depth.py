"""sim_depth: split backfields, measured on any season and won during a simulated one (no hindsight)."""
import json
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_pool, sim_depth, sim_season, sim_world
from services.replay_box import BoxLine
from services.replay_pool import Injury, PoolPlayer
from services.sim_next_year import volume
from services.sim_world import Player, Team, World
from tests import replay_fakes as fakes

A, B, C = 9_001, 9_002, 9_003


def _season(work: dict[int, list[float]]):
    """A fake season whose team XXX has these RBs with this carries+targets per week (None = no game)."""
    data = fakes.fake_season()
    players = {**data.players, **{pid: PoolPlayer(pid, f"Back {pid}", "RB") for pid in work}}
    box = {(pid, w + 1): BoxLine(carries=n) for pid, weeks in work.items() for w, n in enumerate(weeks) if n is not None}
    games = {pid: tuple((w + 1, "XXX") for w, n in enumerate(weeks) if n is not None) for pid, weeks in work.items()}
    return replace(data, players=MappingProxyType(players), box=MappingProxyType(box),
                   team_by_week=MappingProxyType(games))


def test_a_committee_won_in_week_seven_is_measured():
    early, late = [15, 14, 16, 12], [None, 15, 15, 18, 20, 22, 21, 20, 19, 20, 22, 20, 21]
    data = _season({A: early + late, B: [14, 15, 13, 14, None, 14, 13, 6, 5, 4, 6, 5, 4, 6, 5, 4, 5]})
    (found,) = sim_depth.committees(data)
    assert (found["lead"], found["partner"], found["week"], found["leader_won"]) == (A, B, 7, True)
    assert found["ratio"] < 0.5
    assert sim_depth.metrics(data) == {"RB committees per team": 1.0, "RB committees won %": 100.0}


def test_a_bellcow_is_no_committee_and_a_lasting_split_is_never_won():
    assert sim_depth.committees(_season({A: [20] * 17, B: [5] * 17})) == []
    (split,) = sim_depth.committees(_season({A: [12] * 17, B: [11] * 17}))
    assert split["week"] is None


def _world(split: bool) -> World:
    players = {A: Player(A, "RB", "XXX", targets=0.05, carries=0.4, games=17.0),
               B: Player(B, "RB", "XXX", targets=0.05, carries=0.35 if split else 0.1, games=17.0),
               C: Player(C, "RB", "YYY", targets=0.05, carries=0.4, games=17.0)}
    teams = {t: Team(t, 34.0, 26.0, 0.1, 0.1) for t in ("XXX", "YYY")}
    return World(MappingProxyType(players), MappingProxyType(teams))


@pytest.fixture
def always(monkeypatch):
    fitted = sim_depth.stored()
    monkeypatch.setattr(sim_depth, "stored", lambda: {**fitted, "resolve_rate": 1.0})


def test_a_split_backfield_is_won_at_a_seeded_week_and_keeps_its_work(always):
    world = _world(split=True)
    changes = sim_depth.resolutions(world, world, 3, (2, 16))
    assert sorted(c.player_id for c in changes) == [A, B] and len({c.week for c in changes}) == 1
    week = changes[0].week
    after = World(world.players, world.teams, tuple(changes)).at(week)
    assert volume(after.players[A]) + volume(after.players[B]) == pytest.approx(
        volume(world.players[A]) + volume(world.players[B]))
    loser, winner = sorted((after.players[A], after.players[B]), key=volume)
    assert volume(loser) / volume(winner) == pytest.approx(sim_depth.stored()["post_ratio"])
    outcomes = {(c.week, c.player_id, c.kind) for seed in range(30) for c in sim_depth.resolutions(world, world, seed, (2, 16))}
    assert len({w for w, _, _ in outcomes}) > 1 and {(p, k) for _, p, k in outcomes} >= {(A, "breakout"), (B, "breakout")}


def test_no_committee_no_change():
    world = _world(split=False)
    assert sim_depth.resolutions(world, world, 3, (2, 16)) == []


def test_the_public_learns_the_winner_only_from_played_games(monkeypatch):
    """Two whole seasons alike but for one committee won in week LATE: nothing public before LATE differs."""
    late = 12
    data = fakes.fake_season()
    empty = MappingProxyType({})
    prior = sim_world.estimate(data, data.last_week + 1)
    future = replace(data, actual=empty, projected=empty, team_by_week=empty, injuries=empty, usage=empty, box=empty,
                     expert_ranks=empty, expert_age=empty, returns=empty, notes=empty,
                     players=MappingProxyType({p: pl for p, pl in data.players.items() if pl.adp}))
    monkeypatch.setattr(sim_depth, "COMMITTEE_RATIO", 0.0)
    fitted = sim_depth.stored()
    runs = []
    for rate in (0.0, 1.0):
        monkeypatch.setattr(sim_depth, "stored", lambda rate=rate: {**fitted, "resolve_rate": rate,
                                                                       "weeks": {str(late): 1.0}})
        runs.append(sim_season.simulate(future, 5, 1, prior))
    before = [replay_pool._as_of.__wrapped__(run, late) for run in runs]
    for name in ("actual", "projected", "injuries", "box", "usage", "expert_ranks"):
        assert getattr(before[0], name) == getattr(before[1], name), name
    assert {k: v for k, v in runs[0].actual.items() if k[1] >= late} != {k: v for k, v in runs[1].actual.items() if k[1] >= late}


def test_the_news_says_why_a_backup_qb_starts():
    players = {1: Player(1, "QB", "XXX", starts=1.0), 2: Player(2, "QB", "XXX", starts=0.1),
               3: Player(3, "QB", "YYY", starts=1.0), 4: Player(4, "QB", "YYY", starts=0.1)}
    world = World(MappingProxyType(players), MappingProxyType({t: Team(t, 34.0, 26.0, 0.1, 0.1) for t in ("XXX", "YYY")}))
    names = {i: PoolPlayer(i, f"Passer {i}", "QB") for i in players}
    notes = sim_depth.fill_ins(world, {1}, {1: Injury("IR", None, "Knee")}, names, 5)
    assert notes == {(2, 5): "Expected to start while Passer 1 is out (IR, Knee)."}
    assert sim_depth.fill_ins(world, {2}, {}, names, 5) == {}          # a backup out changes nothing


def test_a_refit_keeps_the_tuned_resolve_rate_and_refuses_a_corrupt_file(tmp_path, monkeypatch):
    depth = sim_depth
    monkeypatch.setattr(depth, "PATH", tmp_path / "sim_depth.json")
    assert depth.stored_resolve_rate() == 0.5                              # no file yet
    depth.PATH.write_text(json.dumps({"resolve_rate": 0.37}), encoding="utf-8")
    assert depth.stored_resolve_rate() == 0.37
    depth.PATH.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        depth.stored_resolve_rate()
