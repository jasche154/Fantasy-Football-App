"""Replay League season data: expert ranks and hard-tag returns (fake season and loading them back)."""
import pytest
from sqlalchemy import create_engine, delete
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, ReplayExpertRank
from services import replay_pool
from services.replay_pool import Injury, hard_tag_returns
from tests import replay_fakes as fakes


@pytest.fixture(scope="module")
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, fakes.fake_season())
        yield session
    engine.dispose()


def test_returns_mark_each_hard_tag_streak_with_the_first_later_game_or_never():
    out, ir = Injury("O"), Injury("IR")
    actual = {(1, w): 10.0 for w in (1, 2, 6, 9)} | {(2, 1): 5.0, (3, 4): 1.0}
    injuries = {(1, 3): out, (1, 4): out, (1, 7): ir, (1, 8): Injury("Q"),     # bye in week 5 stays in the streak
                (2, 2): ir, (2, 5): ir,                                       # never back: one open streak
                (3, 4): out, (4, 2): Injury("Q")}                             # played through the tag; soft tag

    assert hard_tag_returns(injuries, actual, 10) == {(1, 3): 6, (1, 7): 9, (2, 2): 0}


def test_fake_season_returns_follow_the_story():
    data = fakes.fake_season()

    assert dict(data.returns) == {(fakes.INJURED_STAR_ID, fakes.INJURED_WEEKS[0]): fakes.STAR_BACK_WEEK,
                                  (fakes.IR_KICKER_ID, fakes.IR_KICKER_WEEK): 0}
    assert data.as_of(fakes.INJURED_WEEKS[0] - 1).returns == {}
    assert dict(data.as_of(fakes.IR_KICKER_WEEK).returns) == dict(data.returns)


def test_fake_expert_ranks_track_belief_with_noise_and_know_only_known_players():
    data = fakes.fake_season()
    ranks = data.expert_ranks

    for week in (0, 1, 8, 17):
        for position in replay_pool.ALL_POSITIONS:
            got = sorted(r for (pid, w), r in ranks.items() if w == week and data.players[pid].position == position)
            assert got == list(range(1, len(got) + 1))                    # a full 1..n order per page
    assert ranks[(fakes.BREAKOUT_ID, 0)] > 30 and ranks[(fakes.BREAKOUT_ID, 8)] <= 10      # experts catch on
    assert (fakes.player_id("K", 33), 1) not in ranks and (fakes.player_id("K", 33), 9) in ranks
    assert data.expert_age[0] == 3 and all(data.expert_age[w] == 2 for w in range(1, 18))


def test_future_changed_scrambles_later_expert_ranks_and_keeps_returns():
    data = fakes.fake_season()
    later = fakes.future_changed(data, 6)

    assert all(later.expert_ranks[k] == v for k, v in data.expert_ranks.items() if k[1] <= 6)
    assert all(later.expert_ranks[k] != v for k, v in data.expert_ranks.items() if k[1] > 6)
    assert later.returns == data.returns
    assert dict(later.as_of(6).expert_ranks) == dict(data.as_of(6).expert_ranks)


def test_returns_changed_alters_only_returns():
    data = fakes.fake_season()
    changed = fakes.returns_changed(data)

    assert set(changed.returns) == set(data.returns)
    assert all(changed.returns[k] != v for k, v in data.returns.items())
    assert changed.expert_ranks is data.expert_ranks and changed.injuries is data.injuries


def test_load_returns_the_same_expert_ranks_ages_and_returns(db):
    data = fakes.fake_season()
    replay_pool.clear_cache()

    loaded = replay_pool.load_season_data(db, fakes.SEASON, "half")

    assert dict(loaded.expert_ranks) == dict(data.expert_ranks)
    assert dict(loaded.expert_age) == dict(data.expert_age)
    assert dict(loaded.returns) == dict(data.returns)


def test_a_season_without_expert_ranks_loads_with_none_and_is_still_ready(db):
    db.execute(delete(ReplayExpertRank))
    db.commit()
    replay_pool.clear_cache()

    status = replay_pool.readiness(db, fakes.SEASON)
    loaded = replay_pool.load_season_data(db, fakes.SEASON, "half")

    assert status["ready"] is True and status["expert_snapshots"] == 0
    assert dict(loaded.expert_ranks) == {} and dict(loaded.expert_age) == {} and loaded.returns
