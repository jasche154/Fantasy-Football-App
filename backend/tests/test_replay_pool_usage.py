"""Replay League usage numbers in SeasonData: loaded from the box scores and snap counts, never ahead of time."""
from dataclasses import replace

import pytest
from sqlalchemy import delete

from database.models import PlayerSnapCountsWeekly
from services import replay_pool
from services.replay_pool import SKILL
from tests import replay_fakes as fakes
from tests.test_replay_pool import loaded, seeded_db, small_db  # noqa: F401  (fixtures)

DATA = fakes.fake_season()


def test_every_skill_game_of_the_fake_season_has_usage_and_nothing_else_does():
    skill_games = {key for key in DATA.actual if DATA.players[key[0]].position in SKILL}

    assert set(DATA.usage) == skill_games
    assert all(u.snap_pct is not None and u.target_share is not None for u in DATA.usage.values())
    back = fakes.player_id("RB", 1)
    assert DATA.usage[(back, 1)].carries > 0 and DATA.usage[(fakes.player_id("QB", 1), 1)].targets == 0


def test_load_reads_the_same_usage_back(loaded):
    assert dict(loaded.usage) == dict(DATA.usage)


def test_without_snap_counts_the_season_is_still_ready_and_only_snap_pct_is_missing(small_db):
    small_db.execute(delete(PlayerSnapCountsWeekly))
    small_db.commit()
    replay_pool.clear_cache()

    without = replay_pool.load_season_data(small_db, fakes.SEASON, "half")

    assert replay_pool.readiness(small_db, fakes.SEASON)["ready"]
    assert dict(without.usage) == {key: replace(u, snap_pct=None) for key, u in DATA.usage.items()}


@pytest.mark.parametrize("week", [0, 1, 6, 17])
def test_as_of_keeps_usage_of_played_weeks_only(week):
    known = DATA.as_of(week)

    assert dict(known.usage) == {key: u for key, u in DATA.usage.items() if key[1] < week}
    assert {pid for pid, _ in known.usage} <= set(known.players)


def test_as_of_usage_is_identical_when_only_the_future_differs():
    later = fakes.future_changed(DATA, 6)

    assert later.as_of(7).usage == DATA.as_of(7).usage
    assert all(later.usage[key] != u for key, u in DATA.usage.items() if key[1] > 6)
    assert later.as_of(8).usage != DATA.as_of(8).usage
