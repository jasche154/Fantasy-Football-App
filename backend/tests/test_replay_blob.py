"""A simulated league's frozen season: the codec is exact and tolerant of old saves, the store keeps one object
per league, and deleting a league deletes its season."""
import json
import zlib
from dataclasses import fields, replace
from types import MappingProxyType

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, ReplaySeasonData
from services import replay_blob, replay_pool, replay_state as rs
from services.replay_pool import SeasonData
from services.replay_scoring import ScoringRules
from tests import replay_fakes as fakes

DATA = fakes.fake_season()


def assert_same(a: SeasonData, b: SeasonData) -> None:
    """Field by field (SeasonData compares by identity), with the types of every key and value."""
    for spec in fields(SeasonData):
        mine, theirs = getattr(a, spec.name), getattr(b, spec.name)
        if spec.name == "schedule":
            assert mine.games == theirs.games and dict(mine.opponents) == dict(theirs.opponents)
            assert isinstance(theirs.games, tuple)
        elif isinstance(mine, MappingProxyType):
            assert isinstance(theirs, MappingProxyType) and dict(mine) == dict(theirs), spec.name
            assert [(type(k), _kind(v)) for k, v in mine.items()] == [(type(k), type(v)) for k, v in theirs.items()]
        else:
            assert mine == theirs, spec.name


def _kind(value) -> type:
    """A numpy number comes back as the Python number it holds."""
    return type(value.item()) if isinstance(value, np.generic) else type(value)


def _rich(data: SeasonData) -> SeasonData:
    """Every field set, numpy numbers included (an engine may hand them back)."""
    some = next(iter(data.players))
    return replace(data, known_before=None, rules=ScoringRules(reception=1.0, te_premium=0.5, pass_td=6.0),
                   adp_format="2qb", notes=MappingProxyType({(some, 3): "Named the starter", (some, 4): "Limited"}),
                   defense_prior=MappingProxyType({("BUF", "QB"): 0.93, ("NYJ", "RB"): np.float64(1.07)}),
                   projected=MappingProxyType({**data.projected, (some, 99): np.float64(4.25)}))


def test_every_field_round_trips_exactly():
    data = _rich(DATA)

    back = replay_blob.loads(replay_blob.dumps(data))

    assert_same(data, back)
    assert all(len(getattr(back, name)) for name in ("players", "actual", "projected", "team_by_week", "injuries",
                                                     "usage", "box", "expert_ranks", "expert_age", "returns", "notes"))
    assert_same(DATA.as_of(5), replay_blob.loads(replay_blob.dumps(DATA.as_of(5))))      # known_before kept


def test_an_old_save_reads_with_defaults_and_a_newer_one_is_refused():
    payload = json.loads(zlib.decompress(replay_blob.dumps(_rich(DATA))))
    for name in ("notes", "rules"):
        del payload[name]                              # saved before these fields existed
    del payload["box"]["v"]["rec_td"]                  # ... and before this column did
    payload["players"]["v"]["retired_field"] = [0] * len(payload["players"]["k"])     # since removed
    payload["future_field"] = 1

    old = replay_blob.loads(zlib.compress(json.dumps(payload).encode()))

    assert old.notes == {} and old.rules is None and old.actual == DATA.actual and old.usage == DATA.usage
    assert {key: replace(box, rec_td=0) for key, box in DATA.box.items()} == old.box
    with pytest.raises(TypeError):                     # Usage.target_share has no default: an unreadable save...
        replay_blob._records({"snap_pct": [1.0]}, fakes.Usage, 1)
    assert "TypeError" in _refusal({**payload, "usage": {"k0": [1], "k1": [1], "v": {"snap_pct": [1.0]}}})
    assert "version 2" in _refusal({**payload, "v": replay_blob.VERSION + 1})
    with pytest.raises(ValueError, match="Not a stored replay season"):
        replay_blob.loads(b"not a blob")


def _refusal(payload: dict) -> str:
    """...is a ValueError, never a half-built season."""
    with pytest.raises(ValueError) as refused:
        replay_blob.loads(zlib.compress(json.dumps(payload).encode()))
    return str(refused.value)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _league(db: Session, data: SeasonData) -> int:
    settings = fakes.fake_settings(sim_from=6)
    row = rs.create(db, "Sim", settings, fakes.drafted_state(), 1, commit=False)
    replay_blob.store(db, row.id, "live", 5, data)
    db.commit()
    return row.id


def test_a_league_gets_one_season_object_until_the_cache_is_cleared(db):
    league = _league(db, DATA)

    first, again = replay_blob.load(db, league), replay_blob.load(db, league)
    replay_pool.clear_cache()
    decoded = replay_blob.load(db, league)

    assert first is DATA and again is DATA                       # the season it was created with
    assert decoded is not DATA and decoded is replay_blob.load(db, league)
    assert_same(DATA, decoded)


def test_a_dropped_season_frees_the_caches_that_key_on_it(db, monkeypatch):
    freed = []
    monkeypatch.setattr(replay_pool, "EVICT_HOOKS", [lambda: freed.append(True)])
    leagues = [_league(db, replace(DATA, season=DATA.season + i)) for i in range(replay_blob.CACHED + 1)]

    assert freed == [True] and len(replay_blob._cache) == replay_blob.CACHED
    assert replay_blob.load(db, leagues[0]).season == DATA.season          # decoded again from the store


def test_deleting_a_league_deletes_its_season(db):
    league = _league(db, DATA)

    rs.delete(db, league)
    replay_blob.forget(db, league)

    assert db.get(ReplaySeasonData, league) is None
    with pytest.raises(replay_blob.NoDataError):
        replay_blob.load(db, league)
