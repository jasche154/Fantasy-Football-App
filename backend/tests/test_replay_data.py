"""Replay League data import: parsing canned site payloads, matching, storing, idempotency (no network)."""
import json
import threading

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import (
    KickerGame, NflGame, Player, PlayerSnapCountsWeekly, PlayerStatsWeekly, ReplayAdp, ReplayProjection, TeamGameStats,
)
from integrations import adp_history, espn_history, sleeper_projections
from integrations.adp_history import AdpEntry, HistoryClient, HistoryError, SeasonMatcher
from services import replay_data, replay_injuries, replay_pool
from tests import replay_fakes as fakes

SEASON = 2019

FFC_PAYLOAD = {"status": "Success", "meta": {"type": "PPR", "teams": 12}, "players": [
    {"player_id": 1, "name": "Christian McCaffrey", "position": "RB", "team": "SF", "adp": 1.5, "stdev": 0.5, "bye": 9},
    {"player_id": 5, "name": "Nobody Unknown", "position": "WR", "team": "FA", "adp": 160.0, "stdev": 9.0, "bye": 0},
    {"player_id": 2, "name": "Mike Williams", "position": "WR", "team": "LAC", "adp": 80.0, "stdev": 6.1, "bye": 12},
    {"player_id": 3, "name": "LA Rams Defense", "position": "DEF", "team": "LAR", "adp": 92.4, "stdev": 8.7, "bye": 9},
    {"player_id": 4, "name": "Michael Badgley", "position": "PK", "team": "LAC", "adp": 150.0, "stdev": 7.0, "bye": 12},
]}
MFL_ADP = {"adp": {"totalDrafts": "10", "player": [
    {"id": "100", "averagePick": "1.2"}, {"id": "200", "averagePick": "30.5"}, {"id": "0501", "averagePick": "110.0"},
    {"id": "300", "averagePick": "120.0"}, {"id": "400", "averagePick": "140.0"}]}}
MFL_PLAYERS = {"players": {"player": [
    {"id": "100", "name": "McCaffrey, Christian", "position": "RB", "team": "CAR"},
    {"id": "200", "name": "Hurt, Rookie", "position": "RB", "team": "FA"},
    {"id": "0501", "name": "Raiders, Oakland", "position": "Def", "team": "OAK"},
    {"id": "300", "name": "Kuechly, Luke", "position": "LB", "team": "CAR"},
    {"id": "400", "name": "Zuerlein, Greg", "position": "PK", "team": "LAR", "espn_id": "14993"}]}}
CROSSWALK = {"100": "00-CMC", "200": "00-HURT"}
# Stands in for nflverse: McCaffrey questionable in week 2; the rookie the ADP step creates is on IR in week 1.
INJURY_REPORTS = [{"season": SEASON, "week": 2, "game_type": "REG", "gsis_id": "00-CMC", "team": "KC",
                   "report_status": "Questionable", "practice_status": "Limited Participation in Practice",
                   "report_primary_injury": "Ankle", "practice_primary_injury": None}]
INJURY_ROSTERS = [{"season": SEASON, "week": 1, "game_type": "REG", "gsis_id": "00-HURT", "team": "KC",
                   "status": "RES", "status_description_abbr": "R01"}]
DOWNLOADS: list[int] = []


def espn_player(player_id: int, name: str, position_id: int, stats: dict | None = None, applied: float = 0.0,
                week: int = 1) -> dict:
    line = {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": week, "appliedTotal": applied,
            "stats": stats or {}}
    return {"player": {"id": player_id, "fullName": name, "defaultPositionId": position_id, "proTeamId": 0,
                       "stats": [line]}}


def espn_payload(week: int) -> dict:
    return {"players": [
        # 80 rush yards, 0.5 rush TD, 4 catches for 30 yards: 14 standard, 16 half, 18 PPR
        espn_player(3117251, "Christian McCaffrey", 2, {"24": 80.0, "25": 0.5, "53": 4.0, "42": 30.0}, 18.0, week),
        espn_player(999, "Mike Williams", 3, {"53": 3.0, "42": 50.0}, 8.0, week),        # no id match: by name
        espn_player(3123052, "Michael Badgley", 5, {"80": 1.0, "86": 2.0}, 8.41, week),
        espn_player(-16013, "Raiders D/ST", 16, {"99": 2.0}, 6.5, week),
        espn_player(-16014, "Rams D/ST", 16, {"99": 2.0}, -0.01, week),                  # projected below zero
        espn_player(555, "Hurt Star", 2, {}, 0.0, week),                                 # injured: projected 0
        espn_player(777, "Some Linebacker", 9, {"99": 0.1}, 0.5, week),
    ]}


def sleeper_row(player_id: str, first: str, last: str, position: str, **stats: float) -> dict:
    return {"player_id": player_id, "week": 1, "stats": stats,
            "player": {"first_name": first, "last_name": last, "position": position, "team": "XXX"}}


SLEEPER_PAYLOAD = [
    sleeper_row("4034", "Christian", "McCaffrey", "RB", pts_ppr=20.0, pts_half_ppr=17.5, pts_std=15.0),
    sleeper_row("88", "Mike", "Williams", "WR", pts_ppr=9.0, pts_std=6.0),               # no half-PPR number
    sleeper_row("5230", "Michael", "Badgley", "K", pts_ppr=7.5, pts_half_ppr=7.5, pts_std=7.5),
    sleeper_row("LAR", "Los Angeles", "Rams", "DEF", pts_ppr=8.0, pts_half_ppr=8.0, pts_std=8.0),
    sleeper_row("77", "Place", "Holder", "WR", adp_dd_ppr=1000.0),
    sleeper_row("78", "Total", "Stranger", "TE", pts_ppr=3.0, pts_half_ppr=2.5, pts_std=2.0),
]


def team_game(season: int, week: int) -> TeamGameStats:
    return TeamGameStats(season=season, week=week, team="KC", opponent="LV", points_for=28, points_against=10,
                         plays=60, dropbacks=35, pass_epa=0.0, rush_epa=0.0, sacks_taken=1, ints_thrown=0,
                         fumbles_lost=0, def_sacks=2, def_ints=1, def_fumble_rec=0, def_tds=0, def_safeties=0,
                         def_blocks=0, rz_trips=3, rz_tds=2, fourth_downs=1, fourth_down_goes=0, fg_att=1,
                         fg_made=1, pat_att=3, pat_made=3)


def kicker_game(season: int, week: int, kicker_id: int) -> KickerGame:
    return KickerGame(season=season, week=week, player_id=kicker_id, team="LAC", opponent="DEN", made_0_39=1,
                      made_40_49=0, made_50=0, missed_0_39=0, missed_40_49=0, missed_50=0, pat_made=2, pat_att=2)


def join_prepare_thread() -> None:
    for thread in threading.enumerate():
        if thread.name == "replay-prepare":
            thread.join(5)


class Sites:
    """Stands in for the four sites; counts requests and can fail one host (or one FFC format's path).
    FFC answers every format with `ffc` under that format's meta type, or `formats[format]` when given."""

    def __init__(self, ffc: dict | None = None, broken: str = "", formats: dict[str, dict] | None = None):
        self.ffc, self.broken, self.requests = ffc or FFC_PAYLOAD, broken, []
        self.formats = formats or {}

    def _ffc(self, path: str) -> dict:
        adp_format, (_, kind) = next((f, v) for f, v in adp_history.FFC_FORMATS.items() if path.endswith(f"/{v[0]}"))
        payload = self.formats.get(adp_format, self.ffc)
        return {**payload, "meta": {**payload["meta"], "type": kind}} if "meta" in payload else payload

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, params = request.url.host, request.url.params
        if self.broken and (self.broken in host or request.url.path.endswith(f"/{self.broken}")):
            return httpx.Response(503)
        if "fantasyfootballcalculator" in host:
            return httpx.Response(200, json=self._ffc(request.url.path))
        if "myfantasyleague" in host:
            return httpx.Response(200, json=MFL_ADP if params["TYPE"] == "adp" else MFL_PLAYERS)
        if "espn" in host:
            return httpx.Response(200, json=espn_payload(int(params["scoringPeriodId"])))
        return httpx.Response(200, json=SLEEPER_PAYLOAD)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


@pytest.fixture
def db(engine, monkeypatch):
    """An empty database with one small 2019 season: four players, two weeks of games, all 32 defenses."""
    monkeypatch.setattr(replay_data, "PAUSE_SECONDS", 0.0)
    monkeypatch.setattr(replay_data, "mfl_crosswalk", lambda: dict(CROSSWALK))
    monkeypatch.setattr(replay_injuries, "download", lambda season: (
        DOWNLOADS.append(season) or (list(INJURY_REPORTS), list(INJURY_ROSTERS))))
    DOWNLOADS.clear()
    with Session(engine) as session:
        players = [Player(gsis_id="00-CMC", full_name="Christian McCaffrey", position="RB", espn_player_id="3117251",
                          sleeper_player_id="4034"),
                   Player(gsis_id="00-MW1", full_name="Mike Williams", position="WR"),        # played in 2019
                   Player(gsis_id="00-MW2", full_name="Mike Williams", position="WR"),        # a namesake who did not
                   Player(gsis_id="00-BADG", full_name="Mike Badgley", position="K", espn_player_id="3123052",
                          sleeper_player_id="5230"),
                   Player(gsis_id="00-ZUER", full_name="Greg Zuerlein", position="K", espn_player_id="14993")]
        session.add_all(players)
        session.flush()
        for week in (1, 2):
            session.add(NflGame(game_id=f"{SEASON}_{week:02d}_OAK_KC", season=SEASON, week=week, game_type="REG",
                                home_team="KC", away_team="OAK", home_score=28, away_score=10))
            for player in players[:2]:
                session.add(PlayerStatsWeekly(player_id=player.id, season=SEASON, week=week, season_type="REG"))
            session.add(kicker_game(SEASON, week, players[3].id))
        replay_data.ensure_defenses(session)
        session.commit()
        yield session


def player_id(session: Session, gsis: str) -> int:
    return session.scalar(select(Player.id).where(Player.gsis_id == gsis))


def adp_by_gsis(session: Session, adp_format: str = "ppr") -> dict[str, tuple[float, str]]:
    rows = session.execute(select(Player.gsis_id, ReplayAdp.adp, ReplayAdp.source).join(
        ReplayAdp, ReplayAdp.player_id == Player.id).where(ReplayAdp.season == SEASON, ReplayAdp.format == adp_format))
    return {gsis: (adp, source) for gsis, adp, source in rows}


# --- site payloads -------------------------------------------------------------------------------------------

def test_ffc_players_are_sorted_by_adp_with_kickers_and_defenses_renamed_and_every_team_kept():
    entries = adp_history.parse_ffc(FFC_PAYLOAD)

    assert [e.name for e in entries] == ["Christian McCaffrey", "Mike Williams", "LA Rams Defense",
                                         "Michael Badgley", "Nobody Unknown"]
    assert [(e.position, e.team) for e in entries] == [("RB", "SF"), ("WR", "LAC"), ("DST", "LA"), ("K", "LAC"),
                                                       ("WR", "FA")]
    assert (entries[0].adp, entries[0].stdev, entries[0].source) == (1.5, 0.5, "ffc")


@pytest.mark.parametrize("payload", [{"status": "Error", "errors": "No ADP data found."},
                                     {"status": "Success", "players": []}, "<html>down</html>"])
def test_ffc_error_and_empty_responses_raise_a_history_error(payload):
    with pytest.raises(HistoryError):
        adp_history.parse_ffc(payload)


def test_mfl_adp_is_joined_to_names_and_defensive_players_are_dropped():
    entries = adp_history.parse_mfl(MFL_ADP, MFL_PLAYERS)

    assert [(e.name, e.position, e.mfl_id) for e in entries] == [
        ("Christian McCaffrey", "RB", "100"), ("Rookie Hurt", "RB", "200"), ("Oakland Raiders", "DST", "0501"),
        ("Greg Zuerlein", "K", "400")]
    assert [e.team for e in entries] == ["CAR", "FA", "LV", "LA"] and entries[3].espn_id == "14993"


def test_mfl_unexpected_response_raises_a_history_error():
    with pytest.raises(HistoryError):
        adp_history.parse_mfl({"error": "nope"}, MFL_PLAYERS)


def test_espn_stat_line_is_scored_in_all_three_formats_with_the_apps_scoring():
    projections = {p.name: p for p in espn_history.parse_week(espn_payload(1), 1)}

    back = projections["Christian McCaffrey"]
    assert (back.position, back.pts_std, back.pts_half, back.pts_ppr) == ("RB", 14.0, 16.0, 18.0)


def test_espn_kickers_and_defenses_carry_espns_total_and_zero_projections_are_dropped():
    projections = {p.name: p for p in espn_history.parse_week(espn_payload(1), 1)}

    kicker, defense = projections["Michael Badgley"], projections["Raiders D/ST"]
    assert (kicker.position, kicker.pts_ppr, kicker.pts_half, kicker.pts_std) == ("K", 8.41, 8.41, 8.41)
    assert (defense.position, defense.team, defense.pts_std) == ("DST", "LV", 6.5)
    assert not {"Rams D/ST", "Hurt Star", "Some Linebacker"} & set(projections)


def test_espn_lines_for_another_week_are_ignored():
    assert espn_history.parse_week(espn_payload(2), 1) == []


def test_espn_request_asks_for_the_whole_pool_including_kickers_and_defenses():
    sites = Sites()

    espn_history.fetch_week(HistoryClient(sites.client(), pause=0), SEASON, 5)

    request = sites.requests[0]
    players = json.loads(request.headers["X-Fantasy-Filter"])["players"]
    assert f"/seasons/{SEASON}/" in request.url.path and request.url.params["scoringPeriodId"] == "5"
    assert players["limit"] == espn_history.POOL_LIMIT and {16, 17} <= set(players["filterSlotIds"]["value"])
    assert players["filterStatsForScoringPeriodIds"]["value"] == [5]
    assert not any(key.startswith("sortApplied") for key in players)      # never "top N by points"


def test_sleeper_rows_keep_three_formats_and_placeholders_are_dropped():
    projections = {p.name: p for p in sleeper_projections.parse_week(SLEEPER_PAYLOAD)}

    back, receiver = projections["Christian McCaffrey"], projections["Mike Williams"]
    assert (back.source_id, back.pts_ppr, back.pts_half, back.pts_std) == ("4034", 20.0, 17.5, 15.0)
    assert (receiver.pts_ppr, receiver.pts_half, receiver.pts_std) == (9.0, 7.5, 6.0)
    assert "Place Holder" not in projections


def test_sleeper_standard_comes_from_half_ppr_and_a_row_with_neither_is_dropped():
    payload = [sleeper_row("90", "Half", "Only", "WR", pts_ppr=10.0, pts_half_ppr=8.0),
               sleeper_row("91", "Ppr", "Only", "WR", pts_ppr=10.0)]

    projections = {p.name: p for p in sleeper_projections.parse_week(payload)}

    assert projections["Half Only"].pts_std == 6.0 and "Ppr Only" not in projections


def test_a_week_whose_payload_parses_to_nothing_is_reported(db):
    class Blank(Sites):
        def __call__(self, request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=[]) if "sleeper" in request.url.host else super().__call__(request)

    summary = replay_data.load_projections(db, SEASON, HistoryClient(Blank().client(), pause=0))

    assert summary["errors"] == {"projections sleeper week 1": "NoRows", "projections sleeper week 2": "NoRows"}


def test_sleeper_defense_is_keyed_by_team_and_scores_the_same_in_every_format():
    defense = next(p for p in sleeper_projections.parse_week(SLEEPER_PAYLOAD) if p.position == "DST")

    assert (defense.team, defense.pts_ppr, defense.pts_half, defense.pts_std) == ("LA", 8.0, 8.0, 8.0)


# --- polite client -------------------------------------------------------------------------------------------

def test_client_retries_once_pauses_between_requests_and_reports_the_error_type_only():
    pauses: list[float] = []
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    client = HistoryClient(http, pause=1.0, sleep=pauses.append)

    with pytest.raises(HistoryError) as error:
        client.get_json("Some site", "https://example.test/secret-path?key=abc")

    assert client.requests == 2 and pauses == [1.0]
    assert "HTTPStatusError" in str(error.value) and "example.test" not in str(error.value)


def test_client_returns_json_when_the_retry_succeeds():
    answers = iter([httpx.Response(502), httpx.Response(200, json={"ok": True})])
    client = HistoryClient(httpx.Client(transport=httpx.MockTransport(lambda request: next(answers))), pause=0)

    assert client.get_json("Some site", "https://example.test/") == {"ok": True}


def test_client_treats_a_non_json_body_as_a_failure():
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text="<html>")))

    with pytest.raises(HistoryError):
        HistoryClient(http, pause=0).get_json("Some site", "https://example.test/")


# --- matching ------------------------------------------------------------------------------------------------

def test_name_match_prefers_the_namesake_who_played_that_season(db):
    matcher = SeasonMatcher(db, SEASON)

    assert matcher.by_name("Mike Williams", "WR") == player_id(db, "00-MW1")


def test_name_match_is_refused_when_two_namesakes_both_played(db):
    db.add(PlayerStatsWeekly(player_id=player_id(db, "00-MW2"), season=SEASON, week=1, season_type="REG"))
    db.commit()

    assert SeasonMatcher(db, SEASON).by_name("Mike Williams", "WR") is None


def test_name_match_uses_aliases_ignores_suffixes_and_tolerates_a_different_position(db):
    matcher = SeasonMatcher(db, SEASON)

    assert matcher.by_name("Michael Badgley", "K") == player_id(db, "00-BADG")
    assert matcher.by_name("Christian McCaffrey Jr.", "WR") == player_id(db, "00-CMC")
    assert matcher.by_name("Nobody Unknown", "WR") is None


def test_a_two_way_player_filed_as_a_defender_is_matched_and_real_defenders_are_not(db, monkeypatch):
    monkeypatch.setitem(adp_history.TWO_WAY, "00-2WAY", "WR")
    db.add_all([Player(gsis_id="00-2WAY", full_name="Two Way", position="CB", espn_player_id="901",
                       sleeper_player_id="902"),
                Player(gsis_id="00-LB", full_name="Luke Kuechly", position="LB", espn_player_id="777",
                       sleeper_player_id="778")])
    db.commit()

    matcher = SeasonMatcher(db, SEASON)

    two_way = player_id(db, "00-2WAY")
    assert matcher.by_gsis["00-2WAY"] == matcher.by_espn["901"] == matcher.by_sleeper["902"] == two_way
    assert matcher.by_name("Two Way", "WR") == two_way
    assert "00-LB" not in matcher.by_gsis and "777" not in matcher.by_espn and "778" not in matcher.by_sleeper
    assert matcher.by_name("Luke Kuechly", "LB") is None


def test_a_two_way_player_is_in_the_pool_at_his_fantasy_position_with_his_games(engine, monkeypatch):
    data = fakes.fake_season()
    two_way, defender = fakes.BUST_ID, fakes.player_id("WR", 5)
    with Session(engine) as session:
        fakes.seed_db(session, data)
        for pid in (two_way, defender):
            session.get(Player, pid).position = "CB"          # how nflverse files a cornerback who also catches
        session.commit()
        monkeypatch.setitem(adp_history.TWO_WAY, session.get(Player, two_way).gsis_id, "WR")

        loaded = replay_pool.load_season_data(session, data.season, data.scoring)

    assert loaded.players[two_way] == data.players[two_way] and loaded.players[two_way].position == "WR"
    for table in ("actual", "projected"):
        mine = {key: value for key, value in getattr(data, table).items() if key[0] == two_way}
        assert mine and {key: value for key, value in getattr(loaded, table).items() if key[0] == two_way} == mine
    assert loaded.team_by_week[two_way] == data.team_by_week[two_way]
    assert defender not in loaded.players and len(loaded.players) == len(data.players) - 1


def test_defenses_match_by_team_code_including_old_codes(db):
    matcher = SeasonMatcher(db, SEASON)

    assert matcher.defense("OAK") == player_id(db, "DST-LV")
    assert matcher.defense("LAR") == player_id(db, "DST-LA")
    assert matcher.defense(None) is None


# --- storing -------------------------------------------------------------------------------------------------

def test_every_team_has_a_defense_and_asking_twice_adds_none(db):
    replay_data.ensure_defenses(db)

    assert db.scalar(select(func.count()).select_from(Player).where(Player.position == "DST")) == 32


def test_old_team_codes_in_the_schedule_are_renamed(db):
    renamed = replay_data.rename_game_teams(db, SEASON)

    assert renamed == 2
    assert set(db.scalars(select(NflGame.away_team).where(NflGame.season == SEASON))) == {"LV"}
    assert replay_data.rename_game_teams(db, SEASON) == 0


def test_mfl_only_players_are_appended_one_pick_apart_after_ffcs_last_pick():
    ffc = [(AdpEntry("ffc", "A", "RB", 1.5, 0.5), 1), (AdpEntry("ffc", "B", "WR", 150.2, 7.0), 2),
           (AdpEntry("ffc", "Unmatched", "WR", 160.0), None)]
    mfl = [(AdpEntry("mfl", "D", "K", 140.0), 4), (AdpEntry("mfl", "A", "RB", 1.2), 1),
           (AdpEntry("mfl", "C", "RB", 30.5), 3)]

    rows = replay_data.adp_rows(SEASON, ffc, mfl)

    assert [(r["player_id"], r["adp"], r["stdev"], r["source"]) for r in rows] == [
        (1, 1.5, 0.5, "ffc"), (2, 150.2, 7.0, "ffc"), (3, 151.2, None, "mfl"), (4, 152.2, None, "mfl")]


def test_adp_is_stored_from_ffc_first_then_mfl_matching_ids_names_kickers_and_defenses(db):
    sites = Sites()

    summary = replay_data.load_adp(db, SEASON, HistoryClient(sites.client(), pause=0))

    assert adp_by_gsis(db) == {
        "00-CMC": (1.5, "ffc"), "00-MW1": (80.0, "ffc"), "DST-LA": (92.4, "ffc"), "00-BADG": (150.0, "ffc"),
        "00-HURT": (151.0, "mfl"), "DST-LV": (152.0, "mfl"), "00-ZUER": (153.0, "mfl")}
    assert summary["ffc_matched"] == "4/5" and summary["mfl_matched"] == "4/4"
    assert summary["unmatched"] == ["Nobody Unknown (WR, ffc 160.0)"]
    assert summary["players_created"] == 1                # the drafted rookie who never played
    teams = dict(db.execute(select(Player.gsis_id, ReplayAdp.team).join(ReplayAdp, ReplayAdp.player_id == Player.id)).all())
    assert teams == {"00-CMC": "SF", "00-MW1": "LAC", "DST-LA": "LA", "00-BADG": "LAC", "00-HURT": "FA",
                     "DST-LV": "LV", "00-ZUER": "LA"}


def test_adp_import_is_idempotent_and_force_fetches_again(db):
    sites = Sites()
    client = HistoryClient(sites.client(), pause=0)
    replay_data.load_adp(db, SEASON, client)
    first, requests = adp_by_gsis(db), len(sites.requests)

    again = replay_data.load_adp(db, SEASON, client)
    forced = replay_data.load_adp(db, SEASON, client, force=True)

    assert again == {"rows": 7, "skipped": True} and forced["rows"] == 7
    assert len(sites.requests) == requests * 2 and adp_by_gsis(db) == first


def test_adp_keeps_ffc_and_reports_the_error_type_when_mfl_is_down(db):
    summary = replay_data.load_adp(db, SEASON, HistoryClient(Sites(broken="myfantasyleague").client(), pause=0))

    assert summary["errors"] == {"adp mfl": "HistoryError"}
    assert {source for _, source in adp_by_gsis(db).values()} == {"ffc"} and summary["rows"] == 4


def test_projections_from_both_sources_are_stored_for_every_regular_season_week(db):
    sites = Sites()

    summary = replay_data.load_projections(db, SEASON, HistoryClient(sites.client(), pause=0))

    rows = {(r.week, r.source, r.player_id): (r.pts_ppr, r.pts_half, r.pts_std) for r in db.scalars(
        select(ReplayProjection).where(ReplayProjection.season == SEASON))}
    back, receiver = player_id(db, "00-CMC"), player_id(db, "00-MW1")
    assert summary["espn"]["rows"] == {1: 4, 2: 4} and summary["sleeper"]["rows"] == {1: 4, 2: 4}
    assert rows[(1, "espn", back)] == (18.0, 16.0, 14.0) and rows[(2, "sleeper", back)] == (20.0, 17.5, 15.0)
    assert rows[(1, "espn", receiver)] == (8.0, 6.5, 5.0)                       # matched by name, not id
    assert rows[(1, "espn", player_id(db, "DST-LV"))] == (6.5, 6.5, 6.5)
    assert rows[(1, "sleeper", player_id(db, "DST-LA"))] == (8.0, 8.0, 8.0)
    assert rows[(2, "sleeper", player_id(db, "00-BADG"))] == (7.5, 7.5, 7.5)
    assert summary["sleeper"]["unmatched"] == ["Total Stranger (TE)"] and summary["sleeper"]["matched"] == "8/10"
    assert len(sites.requests) == 4


def test_projection_import_skips_stored_weeks_and_force_replaces_them(db):
    sites = Sites()
    client = HistoryClient(sites.client(), pause=0)
    replay_data.load_projections(db, SEASON, client)

    again = replay_data.load_projections(db, SEASON, client)
    replay_data.load_projections(db, SEASON, client, force=True)

    assert again["espn"]["rows"] == {1: 4, 2: 4} and len(sites.requests) == 8
    assert db.scalar(select(func.count()).select_from(ReplayProjection)) == 16


def test_a_failing_projection_source_is_reported_by_type_and_the_other_is_still_stored(db):
    summary = replay_data.load_projections(db, SEASON, HistoryClient(Sites(broken="espn").client(), pause=0))

    assert summary["errors"] == {"projections espn": "HistoryError"}
    assert summary["espn"]["rows"] == {} and summary["sleeper"]["rows"] == {1: 4, 2: 4}


# --- the whole import ----------------------------------------------------------------------------------------

@pytest.fixture
def loaders(monkeypatch):
    """populate_stats / advanced_stats loaders replaced by ones that insert a row and record the call."""
    from database import advanced_stats, populate_stats as ps

    calls: list[str] = []

    def stats(session, feed, seasons, index):
        calls.append("stats")
        session.add(PlayerStatsWeekly(player_id=1, season=seasons[0], week=1, season_type="REG"))
        session.commit()

    def schedules(session, seasons):
        calls.append("schedules")
        session.add(NflGame(game_id=f"{seasons[0]}_01_OAK_KC", season=seasons[0], week=1, game_type="REG",
                            home_team="KC", away_team="OAK"))
        session.commit()

    def advanced(session, seasons, index):
        calls.append("advanced")
        session.add_all([team_game(seasons[0], 1), kicker_game(seasons[0], 1, 1)])
        session.commit()

    def snaps(session, seasons, index):
        calls.append("snaps")
        session.add(PlayerSnapCountsWeekly(player_id=1, season=seasons[0], week=1, offense_pct=0.5))
        session.commit()

    monkeypatch.setattr(ps, "load_feed", stats)
    monkeypatch.setattr(ps, "load_snap_counts", snaps)
    monkeypatch.setattr(ps, "load_schedules", schedules)
    monkeypatch.setattr(ps, "sync_crosswalk", lambda session, index: calls.append("crosswalk"))
    monkeypatch.setattr(advanced_stats, "load_advanced_stats", advanced)
    return calls


def test_game_results_load_only_what_is_missing_and_never_the_depth_charts(db, loaders):
    first = replay_data.load_game_results(db, 2018)
    second = replay_data.load_game_results(db, 2018)

    # the second run loaded nothing; the id sync runs every time
    assert loaders == ["stats", "crosswalk", "schedules", "advanced", "crosswalk"]
    assert first["games_renamed"] == 1 and first["nfl_games"] == 1 and second["loaded"] == []
    assert db.scalar(select(NflGame.away_team).where(NflGame.season == 2018)) == "LV"


def test_game_results_that_stay_missing_stop_the_import(db, loaders, monkeypatch):
    from database import advanced_stats

    monkeypatch.setattr(advanced_stats, "load_advanced_stats", lambda session, seasons, index: None)

    with pytest.raises(replay_data.ReplayDataError, match="kicker_games"):
        replay_data.load_game_results(db, 2018)


def test_prepare_season_runs_every_step_and_a_second_run_makes_no_requests(db, loaders, monkeypatch):
    for week in (1, 2):                                   # the fixture season lacks team game stats
        db.add(team_game(SEASON, week))
    db.commit()
    sites, steps = Sites(), []

    summary = replay_data.prepare_season(db, SEASON, http=sites.client(), on_step=steps.append)
    again = replay_data.prepare_season(db, SEASON, http=sites.client())

    assert steps == ["game_results", "snaps", "adp", "experts", "projections", "injuries"]
    assert loaders == ["crosswalk", "snaps", "crosswalk"]           # stats were stored: snaps still load once
    assert summary["snaps"] == {"rows": 1} and again["snaps"] == {"rows": 1, "skipped": "already loaded"}
    assert summary["errors"] == {} and summary["requests"] == 10 and summary["defenses"] == 32   # 4 FFC formats
    assert summary["adp"]["rows"] == 7 and summary["projections"]["weeks"] == 2
    assert summary["injuries"] == {"rows": 2, "weeks": 2, "tags": {"IR": 1, "Q": 1}}   # the rookie is matched
    assert again["requests"] == 0 and again["adp"] == {"rows": 7, "skipped": True}
    assert again["injuries"] == {"rows": 2, "skipped": True} and DOWNLOADS == [SEASON]
    assert replay_pool.readiness(db, SEASON)["injury_weeks"] == 2


def test_prepare_season_reloads_tags_whenever_adp_was_loaded_and_skips_them_without_player_ids(db, loaders,
                                                                                               monkeypatch):
    from database import populate_stats as ps

    for week in (1, 2):
        db.add(team_game(SEASON, week))
    db.commit()
    replay_data.prepare_season(db, SEASON, http=Sites().client())
    db.execute(replay_data.delete(ReplayAdp))                  # ADP gone: the next run loads it and the tags again
    db.commit()

    reloaded = replay_data.prepare_season(db, SEASON, http=Sites().client())
    monkeypatch.setattr(ps, "sync_crosswalk", lambda session, index: 1 / 0)
    unsynced = replay_data.prepare_season(db, SEASON, http=Sites().client())

    assert reloaded["injuries"]["rows"] == 2 and DOWNLOADS == [SEASON, SEASON]
    assert unsynced["injuries"] == {"skipped": "player ids not synced"} and DOWNLOADS == [SEASON, SEASON]


def test_prepare_season_reports_a_missing_ffc_season_by_error_type_and_still_loads_projections(db, loaders):
    for week in (1, 2):
        db.add(team_game(SEASON, week))
    db.commit()
    sites = Sites(ffc={"status": "Error", "errors": "No ADP data found."})

    summary = replay_data.prepare_season(db, SEASON, http=sites.client())

    assert summary["errors"] == {"adp": "FfcNoDataError"} and adp_by_gsis(db) == {}
    assert summary["projections"]["espn"]["rows"] == {1: 4, 2: 4}


def test_seasons_before_2018_are_rejected(db):
    with pytest.raises(ValueError, match="2018"):
        replay_data.prepare_season(db, 2017)
    with pytest.raises(ValueError):
        replay_data.start_prepare(2016)


def test_season_ranges_and_lists_are_parsed():
    assert replay_data.parse_seasons("2018-2020") == [2018, 2019, 2020]
    assert replay_data.parse_seasons("2019, 2023") == [2019, 2023]


# --- background run ------------------------------------------------------------------------------------------

def test_background_prepare_records_progress_and_only_one_runs_at_a_time(engine, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def slow_prepare(session, season, force=False, on_step=None, **_):
        on_step("adp")
        entered.set()
        release.wait(5)
        return {"season": season, "errors": {"adp mfl": "HistoryError"}}

    monkeypatch.setattr(replay_data, "prepare_season", slow_prepare)

    started = replay_data.start_prepare(SEASON, bind=engine)
    assert entered.wait(5)
    with Session(engine) as session:
        running = replay_data.status(session)
    second = replay_data.start_prepare(2021, bind=engine)
    release.set()
    join_prepare_thread()
    with Session(engine) as session:
        done = replay_data.status(session)

    assert (started["status"], started["step"]) == ("running", "starting")
    assert (running["season"], running["status"], running["step"]) == (SEASON, "running", "adp")
    assert (second["season"], second["status"]) == (SEASON, "running")              # 2021 was not started
    assert (done["status"], done["step"], done["error"]) == ("done", None, "HistoryError")


def test_background_prepare_failure_is_stored_as_the_exception_type(engine, monkeypatch):
    def broken_prepare(session, season, **_):
        raise replay_data.ReplayDataError("https://example.test/?key=secret")

    monkeypatch.setattr(replay_data, "prepare_season", broken_prepare)

    replay_data.start_prepare(SEASON, bind=engine)
    join_prepare_thread()
    with Session(engine) as session:
        failed = replay_data.status(session)

    assert (failed["status"], failed["error"]) == ("failed", "ReplayDataError")


def test_a_run_cut_short_by_a_restart_is_reported_as_failed(engine):
    with Session(engine) as session:
        replay_data.system_status.record(session, replay_data.STATUS_KEY,
                                         {"season": SEASON, "status": "running", "step": "adp", "error": None})

        stale = replay_data.status(session)

    assert (stale["status"], stale["error"]) == ("failed", "Interrupted")
