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
                   scoring_adp=MappingProxyType({some: 12.5, some + 1: np.float64(40.25)}),
                   defense_prior=MappingProxyType({("BUF", "QB"): 0.93, ("NYJ", "RB"): np.float64(1.07)}),
                   projected=MappingProxyType({**data.projected, (some, 99): np.float64(4.25)}))


def test_every_field_round_trips_exactly():
    data = _rich(DATA)

    back = replay_blob.loads(replay_blob.dumps(data))

    assert_same(data, back)
    assert all(len(getattr(back, name)) for name in ("players", "actual", "projected", "team_by_week", "injuries",
                                                     "usage", "box", "expert_ranks", "expert_age", "returns", "notes",
                                                     "scores"))
    assert_same(DATA.as_of(5), replay_blob.loads(replay_blob.dumps(DATA.as_of(5))))      # known_before kept


def test_an_old_save_reads_with_defaults_and_a_newer_one_is_refused():
    payload = json.loads(zlib.decompress(replay_blob.dumps(_rich(DATA))))
    for name in ("notes", "rules", "scores", "scoring_adp"):
        del payload[name]                              # saved before these fields existed
    del payload["box"]["v"]["rec_td"]                  # ... and before this column did
    payload["players"]["v"]["retired_field"] = [0] * len(payload["players"]["k"])     # since removed
    payload["future_field"] = 1

    old = replay_blob.loads(zlib.compress(json.dumps(payload).encode()))

    assert old.notes == {} and old.rules is None and old.scores == {} and old.actual == DATA.actual and old.usage == DATA.usage
    assert old.scoring_adp == {} and old.adp_format == "2qb"          # replay_superflex converts its 2-QB list
    assert {key: replace(box, rec_td=0) for key, box in DATA.box.items()} == old.box
    with pytest.raises(TypeError):                     # Usage.target_share has no default: an unreadable save...
        replay_blob._records({"snap_pct": [1.0]}, fakes.Usage, 1)
    assert "TypeError" in _refusal({**payload, "usage": {"k0": [1], "k1": [1], "v": {"snap_pct": [1.0]}}})
    assert "version 2" in _refusal({**payload, "v": replay_blob.VERSION + 1})
    for version in (True, 0, -1, 1.0, "1", None):
        assert "version" in _refusal({**payload, "v": version}), version
    tampered = _refusal({**payload, "v": "<b>" * 2000})          # the route returns the text: only a type name
    assert "version str" in tampered and "<b>" not in tampered and len(tampered) < 200
    with pytest.raises(replay_blob.UnreadableSeason, match=r"can't be read \(error\)"):     # zlib.error
        replay_blob.loads(b"not a blob")


def test_why_a_save_is_unreadable_is_logged_by_type_without_its_contents(caplog):
    with caplog.at_level("WARNING", logger="services.replay_blob"), pytest.raises(replay_blob.UnreadableSeason):
        replay_blob.loads(b"secret-looking bytes")

    assert [record.getMessage() for record in caplog.records] == ["Unreadable season blob (error)"]


def _refusal(payload: dict) -> str:
    """...is an UnreadableSeason, never a half-built season."""
    with pytest.raises(replay_blob.UnreadableSeason) as refused:
        replay_blob.loads(zlib.compress(json.dumps(payload).encode()))
    return str(refused.value)


def test_any_broken_save_is_one_clean_error_with_the_exception_type():
    payload = json.loads(zlib.decompress(replay_blob.dumps(_rich(DATA))))
    short = {**payload["box"], "v": {**payload["box"]["v"], "rec_td": payload["box"]["v"]["rec_td"][:3]}}

    assert "IndexError" in _refusal({**payload, "box": short})                 # a column shorter than its keys
    assert "AttributeError" in _refusal({**payload, "schedule": {**payload["schedule"], "games": [1, 2]}})  # list
    assert "TypeError" in _refusal({**payload, "players": [1, 2, 3]})          # ... where columns belong
    assert "KeyError" in _refusal({**payload, "schedule": {}})
    blob = replay_blob.dumps(_rich(DATA))
    rng = np.random.default_rng(0)
    for cut in (1, 10, len(blob) // 3, len(blob) - 1):
        with pytest.raises(replay_blob.UnreadableSeason):
            replay_blob.loads(blob[:cut])
    for _ in range(40):                                        # flipped bytes: zlib's checksum or the JSON refuses
        garbled = bytearray(blob)
        garbled[int(rng.integers(len(blob)))] ^= 1 << int(rng.integers(8))
        with pytest.raises(replay_blob.UnreadableSeason):
            replay_blob.loads(bytes(garbled))
    for name in ("actual", "injuries", "team_by_week", "players"):     # a table's values garbled: decoded or
        broken = {**payload, name: {**payload[name], "v": "x" if name != "players" else {"name": None}}}
        try:                                                           # refused, never another exception
            replay_blob.loads(zlib.compress(json.dumps(broken).encode()))
        except replay_blob.UnreadableSeason:
            pass


def test_garbage_bytes_and_deeply_nested_json_are_clean_errors():
    with pytest.raises(replay_blob.UnreadableSeason, match=r"\(error\)"):          # zlib.error
        replay_blob.loads(b"not a zlib stream at all")
    deep = b'{"v":1,"players":' + b"[" * 200_000 + b"]" * 200_000 + b"}"
    with pytest.raises(replay_blob.UnreadableSeason, match="RecursionError"):
        replay_blob.loads(zlib.compress(deep))


def test_a_save_that_unpacks_past_the_limit_is_refused_without_unpacking_it_all(monkeypatch):
    blob = replay_blob.dumps(DATA)
    monkeypatch.setattr(replay_blob, "MAX_UNPACKED", len(zlib.decompress(blob)) - 1)
    with pytest.raises(replay_blob.UnreadableSeason, match="unpacks to more than"):
        replay_blob.loads(blob)
    monkeypatch.setattr(replay_blob, "MAX_UNPACKED", len(zlib.decompress(blob)))
    assert_same(DATA, replay_blob.loads(blob))


def test_a_plain_record_holding_a_list_comes_back_hashable_like_a_table_record():
    back = replay_blob._decode(ScoringRules, {"reception": [1, 2]})

    assert back.reception == (1, 2) and hash(back)


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
    replay_blob.store(db, row.id, "live", 5, replay_blob.dumps(data))
    db.commit()
    replay_blob.remember(db, row.id, data)
    return row.id


def test_a_season_is_cached_only_once_its_league_is_committed(db):
    row = rs.create(db, "Sim", fakes.fake_settings(sim_from=6), fakes.drafted_state(), 1, commit=False)
    replay_blob.store(db, row.id, "live", 5, replay_blob.dumps(DATA))
    league = row.id
    db.rollback()                                                   # the commit never happened

    assert not replay_blob._cache
    with pytest.raises(replay_blob.NoDataError):
        replay_blob.load(db, league)


def test_a_new_league_on_a_reused_id_never_sees_the_old_leagues_season(db):
    old = _league(db, DATA)
    replay_blob.load(db, old)
    rs.delete(db, old)                                             # a request that read it before the delete
    replay_blob._keep(replay_blob._key(db, old), DATA)              # ... caches it after the delete's forget
    new = _league(db, replace(DATA, season=DATA.season + 1))

    assert new == old and replay_blob.load(db, new).season == DATA.season + 1


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
