"""Replay League data import when a source fails or sends malformed rows (no network)."""
import pytest
from sqlalchemy import delete, func, select

from database.models import DataLoad, ReplayAdp, ReplayProjection
from integrations import adp_history, espn_history, sleeper_projections
from integrations.adp_history import HistoryClient, HistoryError
from services import replay_data
from tests.test_replay_data import (  # noqa: F401  (db and loaders are fixtures)
    FFC_PAYLOAD, MFL_ADP, MFL_PLAYERS, SEASON, SLEEPER_PAYLOAD, Sites, adp_by_gsis, db, espn_payload, loaders,
    team_game,
)


def sources(session) -> dict[str, int]:
    return dict(session.execute(select(ReplayAdp.source, func.count()).where(
        ReplayAdp.season == SEASON, ReplayAdp.format == "ppr").group_by(ReplayAdp.source)).all())


def broken_crosswalk():
    raise HistoryError("The player id crosswalk could not be loaded (ConnectError).")


# --- ADP: a failed tail is fetched again, a stored season never shrinks ---------------------------------------

def test_mfl_down_on_the_first_run_is_retried_by_the_next_normal_run(db):
    sites = Sites(broken="myfantasyleague")
    client = HistoryClient(sites.client(), pause=0)
    first = replay_data.load_adp(db, SEASON, client)
    sites.broken = ""

    second = replay_data.load_adp(db, SEASON, client)

    assert first["errors"] == {"adp mfl": "HistoryError"} and sources(db) == {"ffc": 4, "mfl": 3}
    assert second["rows"] == 7 and "skipped" not in second


def test_forced_reload_with_mfl_down_keeps_every_stored_row_and_reports_the_error(db):
    sites = Sites()
    client = HistoryClient(sites.client(), pause=0)
    replay_data.load_adp(db, SEASON, client)
    before = adp_by_gsis(db)
    sites.broken = "myfantasyleague"

    forced = replay_data.load_adp(db, SEASON, client, force=True)

    assert forced == {"rows": 7, "kept": True, "errors": {"adp mfl": "HistoryError"}}
    assert adp_by_gsis(db) == before


def formats(session) -> dict[str, set[str]]:
    rows = session.execute(select(ReplayAdp.format, ReplayAdp.source).where(ReplayAdp.season == SEASON)).all()
    return {fmt: {source for f, source in rows if f == fmt} for fmt in {f for f, _ in rows}}


def test_a_ppr_only_season_gets_its_other_formats_from_ffc_while_mfl_is_down(db, caplog):
    sites = Sites()
    client = HistoryClient(sites.client(), pause=0)
    replay_data.load_adp(db, SEASON, client)
    db.execute(delete(ReplayAdp).where(ReplayAdp.season == SEASON, ReplayAdp.format != "ppr"))   # a legacy season
    db.commit()
    ppr = adp_by_gsis(db)
    sites.broken = "myfantasyleague"

    summary = replay_data.load_adp(db, SEASON, client)

    assert summary["errors"] == {"adp mfl": "HistoryError"} and set(summary["formats"]) == {"half", "std", "2qb"}
    assert formats(db) == {"ppr": {"ffc", "mfl"}, "half": {"ffc"}, "std": {"ffc"}, "2qb": {"ffc"}}
    assert adp_by_gsis(db) == ppr                                       # the stored list is never touched
    assert "no MFL tail" in caplog.text
    sites.broken = ""
    replay_data.load_adp(db, SEASON, client)                            # the tails come on the next normal run
    assert formats(db) == {fmt: {"ffc", "mfl"} for fmt in ("ppr", "half", "std", "2qb")}


def test_a_failed_ffc_format_is_logged_with_its_type_only(db, caplog):
    replay_data.load_adp(db, SEASON, HistoryClient(Sites(broken="standard").client(), pause=0))

    assert "no std list from FFC (HistoryError)" in caplog.text


def test_a_failing_crosswalk_stores_no_mfl_rows_and_the_next_run_stores_them(db, monkeypatch):
    client = HistoryClient(Sites().client(), pause=0)
    working = replay_data.mfl_crosswalk
    monkeypatch.setattr(replay_data, "mfl_crosswalk", broken_crosswalk)

    first = replay_data.load_adp(db, SEASON, client)
    stored_first = sources(db)
    monkeypatch.setattr(replay_data, "mfl_crosswalk", working)
    replay_data.load_adp(db, SEASON, client)

    assert first["errors"] == {"adp crosswalk": "HistoryError"} and stored_first == {"ffc": 4}
    assert sources(db) == {"ffc": 4, "mfl": 3}


def test_crosswalk_download_failure_raises_a_history_error_with_the_type_only(monkeypatch):
    import nflreadpy

    def down():
        raise ConnectionError("https://example.test/?key=secret")

    monkeypatch.setattr(nflreadpy, "load_ff_playerids", down)

    with pytest.raises(HistoryError) as error:
        replay_data.mfl_crosswalk()

    assert "ConnectionError" in str(error.value) and "example.test" not in str(error.value)


# --- the id sync runs on every prepare, and nothing is stored without it --------------------------------------

def test_a_failing_id_sync_holds_back_adp_and_projections_until_a_run_where_it_works(db, loaders, monkeypatch):
    from database import populate_stats as ps

    for week in (1, 2):
        db.add(team_game(SEASON, week))
    db.commit()

    def broken(session, index):
        raise RuntimeError("https://example.test/?key=secret")

    monkeypatch.setattr(ps, "sync_crosswalk", broken)
    failed = replay_data.prepare_season(db, SEASON, http=Sites().client())
    counts_after_failure = (db.scalar(select(func.count()).select_from(ReplayAdp)),
                            db.scalar(select(func.count()).select_from(ReplayProjection)))
    monkeypatch.setattr(ps, "sync_crosswalk", lambda session, index: None)
    fixed = replay_data.prepare_season(db, SEASON, http=Sites().client())

    assert failed["errors"] == {"crosswalk": "RuntimeError"} and failed["requests"] == 0
    assert failed["adp"] == failed["projections"] == {"skipped": "player ids not synced"}
    assert counts_after_failure == (0, 0)
    assert fixed["errors"] == {} and fixed["adp"]["rows"] == 7
    assert db.scalar(select(func.count()).select_from(ReplayProjection)) == 16


# --- only seasons the live model reads stamp a data load ------------------------------------------------------

def data_loads(session) -> int:
    return session.scalar(select(func.count()).select_from(DataLoad)) or 0


def test_preparing_a_season_before_first_season_adds_no_data_load(db, loaders):
    from database import populate_stats as ps

    replay_data.load_game_results(db, ps.FIRST_SEASON - 1)

    assert data_loads(db) == 0


def test_preparing_a_season_from_first_season_on_still_adds_a_data_load(db, loaders):
    from database import populate_stats as ps

    replay_data.load_game_results(db, ps.FIRST_SEASON)

    assert data_loads(db) == 1


# --- one malformed row is skipped, never the whole import ----------------------------------------------------

@pytest.mark.parametrize("bad", ["junk", ["a", "list"], None, 7,
                                 {"name": "X", "position": "RB", "adp": 3.0, "team": 12}])
def test_ffc_skips_a_malformed_row_and_keeps_the_good_ones(bad):
    payload = {**FFC_PAYLOAD, "players": [*FFC_PAYLOAD["players"], bad]}

    assert len(adp_history.parse_ffc(payload)) == 5


@pytest.mark.parametrize("players", [["junk", None], {"name": "x"}, []])
def test_ffc_without_any_readable_player_raises_a_history_error(players):
    with pytest.raises(HistoryError):
        adp_history.parse_ffc({"status": "Success", "players": players})


@pytest.mark.parametrize("bad", ["junk", ["100"], None])
def test_mfl_skips_a_malformed_pick_and_keeps_the_good_ones(bad):
    adp = {"adp": {"player": [*MFL_ADP["adp"]["player"], bad]}}
    players = {"players": {"player": [*MFL_PLAYERS["players"]["player"], "junk"]}}

    assert len(adp_history.parse_mfl(adp, players)) == 4


def espn_with(bad) -> dict:
    return {"players": [*espn_payload(1)["players"], bad]}


@pytest.mark.parametrize("bad", [
    "junk", None, {"player": "junk"},
    {"player": {"id": "abc", "fullName": "Bad D", "defaultPositionId": 16, "stats": []}},
    {"player": {"id": -16001, "fullName": "Bad D", "defaultPositionId": 16,
                "stats": [{"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 1, "appliedTotal": "x"}]}},
    {"player": {"id": 5, "fullName": "Bad K", "defaultPositionId": 5, "stats": ["junk"]}},
])
def test_espn_skips_a_malformed_entry_and_keeps_the_good_ones(bad):
    good = {p.name for p in espn_history.parse_week(espn_payload(1), 1)}

    assert {p.name for p in espn_history.parse_week(espn_with(bad), 1)} == good


@pytest.mark.parametrize("bad", [
    "junk", {"player_id": "9", "stats": ["pts_ppr"], "player": {}},
    {"player_id": "9", "stats": {"pts_ppr": 5.0}, "player": ["WR"]},
])
def test_sleeper_skips_a_malformed_row_and_keeps_the_good_ones(bad):
    good = {p.name for p in sleeper_projections.parse_week(SLEEPER_PAYLOAD)}

    assert {p.name for p in sleeper_projections.parse_week([*SLEEPER_PAYLOAD, bad])} == good
