"""Replay League import: the optional "snaps" step (snap counts into the shared table; no network)."""
import pytest
from sqlalchemy import func, select

from database.models import DataLoad, PlayerSnapCountsWeekly
from services import replay_data
from tests.test_replay_data import (  # noqa: F401  (db and loaders are fixtures)
    SEASON, Sites, db, loaders, team_game,
)


def snap_rows(session, season: int = SEASON) -> int:
    return session.scalar(select(func.count()).select_from(PlayerSnapCountsWeekly).where(
        PlayerSnapCountsWeekly.season == season)) or 0


def with_team_games(session):
    for week in (1, 2):
        session.add(team_game(SEASON, week))
    session.commit()


def test_snaps_load_once_then_skip_and_force_loads_again(db, loaders):
    first = replay_data.load_snaps(db, SEASON)
    second = replay_data.load_snaps(db, SEASON)
    forced = replay_data.load_snaps(db, SEASON, force=True)

    assert first == {"rows": 1, "errors": {}} and second == {"rows": 1, "skipped": "already loaded"}
    assert forced["rows"] == 2 and loaders == ["snaps", "snaps"]      # the fake loader appends, the real one replaces


@pytest.mark.parametrize("broken, error", [(RuntimeError("https://example.test/?key=secret"), "RuntimeError"),
                                           (None, "NoRows")])
def test_a_failed_snap_load_is_reported_by_type_and_every_later_step_still_runs(db, loaders, monkeypatch, broken,
                                                                                error):
    from database import populate_stats as ps

    def load(session, seasons, index):               # raises, or stores nothing (a failed download is logged only)
        if broken:
            raise broken

    with_team_games(db)
    monkeypatch.setattr(ps, "load_snap_counts", load)

    summary = replay_data.prepare_season(db, SEASON, http=Sites().client())

    assert summary["errors"] == {"snaps": error} and "secret" not in str(summary)
    assert summary["adp"]["rows"] == 7 and summary["projections"]["espn"]["rows"] == {1: 4, 2: 4}
    assert summary["injuries"]["rows"] == 2 and snap_rows(db) == 0


def test_snaps_wait_for_the_player_id_sync_they_are_matched_by(db, loaders, monkeypatch):
    from database import populate_stats as ps

    with_team_games(db)
    monkeypatch.setattr(ps, "sync_crosswalk", lambda session, index: 1 / 0)

    summary = replay_data.prepare_season(db, SEASON, http=Sites().client())

    assert summary["snaps"] == {"skipped": "player ids not synced"} and "snaps" not in loaders


@pytest.mark.parametrize("live", [False, True])
def test_only_a_season_the_live_model_reads_stamps_a_data_load_for_its_snaps(db, loaders, live):
    from database import populate_stats as ps

    season = ps.FIRST_SEASON if live else ps.FIRST_SEASON - 1

    replay_data.load_snaps(db, season)

    assert snap_rows(db, season) == 1
    assert db.scalar(select(func.count()).select_from(DataLoad)) == int(live)
