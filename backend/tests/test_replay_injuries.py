"""Replay League injury tags: nflverse report and roster rows -> one tag per player-week (no network)."""
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import NflGame, Player, ReplayInjury
from services import replay_injuries as ri

SEASON = 2023
IDS = {"00-A": 1, "00-B": 2, "00-C": 3}
LAST = 6
PLAYING = {(team, week) for team in ("GB", "NYJ") for week in range(1, LAST + 2) if (team, week) != ("NYJ", 4)}


def report(gsis="00-A", week=1, status="Out", practice=None, text=None, other=None, team="NYJ", game_type="REG"):
    return {"season": SEASON, "week": week, "game_type": game_type, "gsis_id": gsis, "team": team,
            "report_status": status, "practice_status": practice, "report_primary_injury": text,
            "practice_primary_injury": other}


def roster(gsis="00-A", week=1, status="RES", abbr="R01", team="NYJ", game_type="REG"):
    return {"season": SEASON, "week": week, "game_type": game_type, "gsis_id": gsis, "team": team,
            "status": status, "status_description_abbr": abbr}


def rows(reports=(), rosters=(), last_week=LAST) -> dict[tuple[int, int], tuple]:
    out = ri.injury_rows(SEASON, list(reports), list(rosters), IDS, PLAYING, last_week)
    assert out == sorted(out, key=lambda r: (r["week"], r["player_id"])) and all(r["season"] == SEASON for r in out)
    return {(r["player_id"], r["week"]): (r["tag"], r["practice"], r["injury"]) for r in out}


@pytest.mark.parametrize(("status", "abbr", "tag"), [
    ("PUP", None, "PUP"), ("RES", "R04", "PUP"), ("SUS", None, "SUS"), ("RES", "R40", "SUS"), ("RES", "R33", "SUS"),
    ("RES", "R30", "SUS"), ("RES", "R01", "IR"), ("RES", None, "IR"), ("RES", "R48", "IR"), ("RSN", None, "IR"),
    ("RES", "R02", None), ("RES", "R03", None), ("RES", "R06", None), ("RES", "R62", None), ("RES", "R59", "IR"),
    ("NWT", None, None), ("ACT", None, None), ("INA", None, None), ("CUT", None, None),
    ("RET", None, None), (None, None, None)])
def test_roster_tag(status, abbr, tag):
    assert ri.roster_tag(status, abbr) == tag


def test_report_status_gives_q_d_o_and_nothing_else():
    got = rows([report(week=1, status="Questionable"), report(week=2, status="Doubtful"), report(week=3),
                report(week=4, status="Note"), report(week=5, status=None)])

    assert {k: v[0] for k, v in got.items()} == {(1, 1): "Q", (1, 2): "D", (1, 3): "O"}


def test_only_regular_season_weeks_in_range_of_known_players_count_and_float_weeks_are_cast():
    got = rows([report(week=2.0), report(week=3, game_type="WC"), report(gsis="00-X", week=2),
                report(week=0), report(week=LAST + 1)])

    assert set(got) == {(1, 2)}


def test_the_first_report_row_of_a_player_week_wins():
    got = rows([report(status="Questionable", text="Knee"), report(status="Out", text="Ankle")])

    assert got == {(1, 1): ("Q", None, "Knee")}


def test_a_roster_tag_wins_over_a_report_tag_and_an_active_row_cancels_a_reserve_row():
    got = rows([report(week=1, status="Questionable", practice="Limited Participation in Practice", text="Knee"),
                report(gsis="00-B", week=1, status="Out", text="Hand")],
               [roster(week=1, status="RES", abbr="R01"),
                roster(gsis="00-B", week=1, status="RES", team="GB"), roster(gsis="00-B", week=1, status="ACT")])

    assert got == {(1, 1): ("IR", "LP", "Knee"), (2, 1): ("O", None, "Hand")}


def test_practice_codes_and_text_are_read_and_cut():
    long = "x" * 80
    got = rows([report(week=1, practice="Did Not Participate In Practice", text="  Hamstring "),
                report(week=2, practice="Full Participation in Practice", other="Toe"),
                report(week=3, practice="\n    ", text=long)], last_week=3)

    assert got == {(1, 1): ("O", "DNP", "Hamstring"), (1, 2): ("O", "FP", "Toe"), (1, 3): ("O", None, "x" * 60)}


def test_a_suspension_never_has_practice_or_injury_text():
    got = rows([report(week=1, status="Questionable", text="Knee"),
                report(week=2, status="Out", practice="Limited Participation in Practice", text="Knee")],
               [roster(week=2, status="SUS", abbr=None)])

    assert got[(1, 2)] == ("SUS", None, None)


def test_injury_text_is_carried_one_week_onto_a_hard_tag():
    got = rows([report(week=5, status="Questionable", text="Knee")], [roster(week=6), roster(week=7)], last_week=7)

    assert got == {(1, 5): ("Q", None, "Knee"), (1, 6): ("IR", None, "Knee"), (1, 7): ("IR", None, "Knee")}


def test_the_text_chain_runs_week_by_week_and_from_an_untagged_report_row():
    got = rows([report(week=1, status="Note", text="Knee")], [roster(week=2), roster(week=3)], last_week=3)

    assert got == {(1, 2): ("IR", None, "Knee"), (1, 3): ("IR", None, "Knee")}


def test_no_text_is_carried_from_two_weeks_back_onto_illness_lists_or_onto_q_and_d():
    got = rows([report(week=1, status="Out", text="Knee"), report(week=3, status="Questionable"),
                report(gsis="00-B", week=1, status="Out", text="Ankle"),
                report(gsis="00-C", week=1, status="Out", text="Hip")],
               [roster(week=3, status="RES", abbr="R01"),               # week 2: nothing at all
                roster(gsis="00-B", week=2, abbr="R59"), roster(gsis="00-C", week=2, abbr="R05")])

    assert got[(1, 3)] == ("IR", None, None)
    assert got[(2, 2)] == ("IR", None, None) and got[(3, 2)] == ("IR", None, None)


def test_a_question_mark_never_takes_carried_text():
    got = rows([report(week=1, status="Out", text="Knee"), report(week=2, status="Questionable")])

    assert got[(1, 2)] == ("Q", None, None)


def test_a_hard_tag_is_carried_over_a_bye_and_its_text_chain_continues():
    # NYJ rests in week 4; GB plays every week
    got = rows([report(week=3, status="Out", practice="Did Not Participate In Practice", text="Knee"),
                report(gsis="00-B", week=3, status="Questionable", text="Toe"),
                report(gsis="00-C", week=3, status="Out", text="Hip", team="GB")],
               [roster(week=5)])

    assert got[(1, 4)] == ("O", None, "Knee") and got[(1, 5)] == ("IR", None, "Knee")
    assert (2, 4) not in got                                       # Q is not carried
    assert (3, 4) not in got                                       # his team played: he simply has no tag


def test_a_reserve_tag_is_carried_over_a_bye_by_its_roster_team_in_todays_code():
    playing = {("LV", w) for w in range(1, LAST + 1) if w != 4}
    out = ri.injury_rows(SEASON, [], [roster(week=3, team="OAK")], IDS, playing, LAST)

    assert [(r["week"], r["tag"]) for r in out] == [(3, "IR"), (4, "IR")]


def test_a_left_squad_row_has_no_tag_and_last_weeks_hard_tag_is_not_carried_onto_it():
    got = rows([report(week=1, status="Out", text="Knee")], [roster(week=2, abbr="R06")])

    assert got == {(1, 1): ("O", None, "Knee")}


def test_text_naming_no_injury_is_never_carried_but_stays_on_its_own_week():
    got = rows([report(week=1, status="Note", text="Not injury related - resting player"),
                report(gsis="00-B", week=1, status="Out", text="Not Injury Related")],
               [roster(week=2), roster(week=3), roster(gsis="00-B", week=2)], last_week=3)

    assert got[(1, 2)] == ("IR", None, None) and got[(1, 3)] == ("IR", None, None)
    assert got[(2, 1)] == ("O", None, "Not Injury Related") and got[(2, 2)] == ("IR", None, None)


def test_text_naming_no_injury_is_not_carried_on_after_a_bye():
    # NYJ rests in week 4
    got = rows([report(week=3, status="Out", text="Not injury related - personal matter")], [roster(week=5)])

    assert got[(1, 4)] == ("O", None, "Not injury related - personal matter") and got[(1, 5)] == ("IR", None, None)


@pytest.mark.parametrize(("text", "carried"), [("Rest", None), ("Coach's decision", None), ("Knee", "Knee")])
def test_rest_and_coach_decisions_are_not_carried_but_an_injury_is(text, carried):
    got = rows([report(week=1, status="Out", text=text)], [roster(week=2)])

    assert got[(1, 2)] == ("IR", None, carried)


@pytest.mark.parametrize(("rosters", "tag"), [([roster(status="ACT")], None), ([], "O"),
                                               ([roster(status="ACT", team="GB")], "O")])
def test_another_clubs_report_is_dropped_when_he_is_on_a_roster_of_a_different_club(rosters, tag):
    got = rows([report(team="GB")], rosters)

    assert got.get((1, 1), (None,))[0] == tag


def test_nothing_is_carried_past_the_last_week():
    assert set(rows([report(week=3)], last_week=3)) == {(1, 3)}


def test_importing_the_module_does_not_load_nflreadpy():
    backend = Path(__file__).resolve().parents[1]
    code = "import sys, services.replay_data; assert 'nflreadpy' not in sys.modules, 'loaded'"
    done = subprocess.run([sys.executable, "-c", code], cwd=backend, capture_output=True, text=True, timeout=120)

    assert done.returncode == 0, done.stderr[-500:]


# ---------------- load_injuries ----------------

@pytest.fixture
def db(engine, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(ri, "download", lambda season: calls.append(season) or (
        [report(week=1, text="Knee"), report(gsis="00-K", week=2, status="Questionable")],
        [roster(gsis="00-B", week=2, status="PUP", abbr=None), roster(gsis="00-Z", week=1)]))
    with Session(engine) as session:
        session.add_all([Player(gsis_id="00-A", full_name="A", position="RB"),
                         Player(gsis_id="00-B", full_name="B", position="WR"),
                         Player(gsis_id="00-K", full_name="K", position="K"),
                         Player(gsis_id="00-Z", full_name="Z", position="LB")])         # not a fantasy position
        session.add_all([NflGame(game_id=f"{SEASON}_{w:02d}_GB_NYJ", season=SEASON, week=w, game_type="REG",
                                 home_team="NYJ", away_team="GB") for w in (1, 2)])
        session.commit()
        session.calls = calls
        yield session


def _stored(session: Session) -> list[tuple]:
    return sorted(session.execute(select(Player.gsis_id, ReplayInjury.week, ReplayInjury.tag, ReplayInjury.injury)
                                  .join(Player, Player.id == ReplayInjury.player_id)).all())


def test_load_stores_the_season_and_a_second_run_skips_the_download(db):
    first = ri.load_injuries(db, SEASON)
    again = ri.load_injuries(db, SEASON)

    assert first == {"rows": 3, "weeks": 2, "tags": {"O": 1, "Q": 1, "PUP": 1}}
    assert _stored(db) == [("00-A", 1, "O", "Knee"), ("00-B", 2, "PUP", None), ("00-K", 2, "Q", None)]
    assert again == {"rows": 3, "skipped": True} and db.calls == [SEASON]


def test_force_replaces_the_seasons_rows_and_leaves_other_seasons(db):
    a = db.scalar(select(Player.id).where(Player.gsis_id == "00-A"))
    db.add_all([ReplayInjury(season=SEASON, week=9, player_id=a, tag="SUS"),
                ReplayInjury(season=SEASON - 1, week=1, player_id=a, tag="IR")])
    db.commit()

    result = ri.load_injuries(db, SEASON, force=True)

    assert result["rows"] == 3 and db.calls == [SEASON]
    assert db.scalar(select(func.count()).select_from(ReplayInjury).where(ReplayInjury.season == SEASON)) == 3
    assert db.scalar(select(ReplayInjury.tag).where(ReplayInjury.season == SEASON - 1)) == "IR"


def test_a_failed_download_keeps_what_is_stored_and_reports_the_type_only(db, monkeypatch):
    ri.load_injuries(db, SEASON)

    def broken(season):
        raise ConnectionError("https://example.invalid/?key=secret")

    monkeypatch.setattr(ri, "download", broken)
    result = ri.load_injuries(db, SEASON, force=True)

    assert result == {"rows": 3, "errors": {"injuries": "ConnectionError"}}
    assert len(_stored(db)) == 3


def test_a_two_way_player_stored_under_another_position_gets_his_tag(db, monkeypatch):
    gsis = next(iter(ri.TWO_WAY))
    db.add(Player(gsis_id=gsis, full_name="Two Way", position="CB"))
    db.commit()
    monkeypatch.setattr(ri, "download", lambda season: ([report(gsis=gsis, week=1, status="Doubtful")], []))

    assert ri.load_injuries(db, SEASON)["tags"] == {"D": 1} and _stored(db) == [(gsis, 1, "D", None)]
