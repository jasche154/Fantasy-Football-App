"""replay_live_data.load_live_adp: MFL's list as the live season's PPR ADP, never shrinking a stored list to nothing."""
import pytest
from sqlalchemy import insert, select

from database.models import Player, ReplayAdp
from integrations import adp_history
from integrations.adp_history import AdpEntry, HistoryError
from services import replay_data, replay_live_data

SEASON = 2026


@pytest.fixture
def ffc_has_none(monkeypatch):
    def no_ffc(*args, **kwargs):
        raise adp_history.FfcNoDataError("no list")

    monkeypatch.setattr(replay_data, "load_adp", no_ffc)
    monkeypatch.setattr(replay_data, "mfl_crosswalk", lambda: {})
    monkeypatch.setattr(replay_data, "create_drafted_players", lambda *args: 0)
    monkeypatch.setattr(replay_live_data, "SeasonMatcher", lambda session, season: None)
    monkeypatch.setattr(adp_history, "fetch_mfl", lambda client, season: [AdpEntry("mfl", "Nobody Known", "WR", 12.0)])


def _stored(session) -> list[tuple[int, float]]:
    return [tuple(row) for row in session.execute(select(ReplayAdp.player_id, ReplayAdp.adp).where(
        ReplayAdp.season == SEASON, ReplayAdp.format == "ppr").order_by(ReplayAdp.player_id))]


def test_an_mfl_answer_matching_nobody_keeps_the_stored_list(session, ffc_has_none, monkeypatch):
    pid = session.scalars(select(Player.id)).first()
    session.execute(insert(ReplayAdp), [{"season": SEASON, "player_id": pid, "format": "ppr", "adp": 3.5,
                                         "stdev": None, "source": adp_history.MFL}])
    session.commit()
    monkeypatch.setattr(replay_data, "adp_player", lambda *args: None)

    with pytest.raises(HistoryError, match="none of its 1"):
        replay_live_data.load_live_adp(session, SEASON, client=None)
    session.rollback()
    assert _stored(session) == [(pid, 3.5)]


def test_matched_rows_replace_the_stored_list(session, ffc_has_none, monkeypatch):
    first, second = session.scalars(select(Player.id).limit(2)).all()
    session.execute(insert(ReplayAdp), [{"season": SEASON, "player_id": first, "format": "ppr", "adp": 3.5,
                                         "stdev": None, "source": adp_history.MFL}])
    session.commit()
    monkeypatch.setattr(replay_data, "adp_player", lambda *args: second)

    result = replay_live_data.load_live_adp(session, SEASON, client=None)
    assert result["rows"] == 1 and _stored(session) == [(second, 12.0)]
