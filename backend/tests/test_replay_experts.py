"""Replay League expert ranks: page parsing, matching, point-in-time weeks and the import step (no network)."""
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database.models import NflGame, Player, ReplayExpertRank
from integrations.adp_history import SeasonMatcher
from services import replay_experts
from services.replay_experts import expert_rows, week_ranks

SEASON = 2023
WEEK1 = date(2023, 9, 7)                # week 1's first kickoff (the Thursday opener)
LAST = date(2024, 1, 7)


def line(page: str, scraped: str, fp_id: str, name: str, ecr: float, team: str = "KC") -> dict:
    return {"fp_page": page, "scrape_date": scraped, "id": fp_id, "player": name, "ecr": ecr, "team": team}


RAW = [
    line("/nfl/rankings/ppr-wr-cheatsheets.php", "2023-08-31", "1", "Old Sheet", 1.0),       # older cheatsheet
    line("/nfl/rankings/ppr-wr-cheatsheets.php", "2023-09-06", "2", "Wide Two", 2.0),
    line("/nfl/rankings/ppr-wr-cheatsheets.php", "2023-09-06", "1", "Wide One", 1.5),
    line("/nfl/rankings/ppr-wr-cheatsheets.php", "2023-09-06", "9", "Nobody Known", 1.0),    # unmatched, still ranked
    line("/nfl/rankings/ppr-wr-cheatsheets.php", "2023-09-08", "1", "Wide One", 9.0),       # after the opener
    line("dst-cheatsheets", "2023-09-06", "8000", "Kansas City Chiefs", 1.0, "KC"),
    line("ros-ppr-wr", "2023-09-15", "1", "Wide One", 3.0),
    line("ros-ppr-wr", "2023-09-15", "2", "Wide Two", 1.0),
    line("/nfl/rankings/ros-ppr-wr.php", "2023-09-29", "2", "Wide Two", 2.0),
    line("/nfl/rankings/ros-ppr-wr.php", "2023-09-29", "x", "Wide One", 1.0),               # matched by name
    line("ros-ppr-wr", "2023-09-01", "1", "Wide One", 1.0),                                  # before the season
    line("ros-ppr-wr", "2024-01-12", "1", "Wide One", 1.0),                                  # after the last game
    line("ros-db", "2023-09-15", "5", "Some Corner", 1.0),                                    # not a fantasy page
    line("ros-ppr-wr", "2023-09-22", "1", "Wide One", None),                                 # no ecr
]


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        session.add_all([Player(gsis_id="00-W1", full_name="Wide One", position="WR"),
                         Player(gsis_id="00-W2", full_name="Wide Two", position="WR"),
                         Player(gsis_id="DST_KC", full_name="Chiefs D/ST", position="DST", nfl_team="KC")])
        for week, day in ((1, "2023-09-07"), (1, "2023-09-10"), (2, "2023-09-17"), (18, "2024-01-07")):
            session.add(NflGame(game_id=f"{SEASON}_{week:02d}_{day}", season=SEASON, week=week, game_type="REG",
                                home_team="KC", away_team="DET", gameday=day))
        session.commit()
        yield session


def ids(session: Session) -> dict[str, int]:
    return dict(session.execute(select(Player.full_name, Player.id)).all())


@pytest.mark.parametrize(("page", "expected"), [
    ("/nfl/rankings/ppr-rb-cheatsheets.php", ("pre", "RB")), ("qb-cheatsheets", ("pre", "QB")),
    ("/nfl/rankings/ros-k.php", ("ros", "K")), ("ros-dst", ("ros", "DST")), ("ros-ppr-te", ("ros", "TE")),
    ("ros-lb", None), ("ppr-wr", None), ("ros-wr-cheatsheets", None), (None, None)])
def test_only_the_six_position_pages_are_read(page, expected):
    assert replay_experts._page(page) == expected


def test_rows_keep_the_last_cheatsheet_and_in_season_ros_pages_matched_by_id_name_and_team(db):
    people = ids(db)

    rows = expert_rows(SEASON, RAW, SeasonMatcher(db, SEASON), {"1": "00-W1", "2": "00-W2"}, WEEK1, LAST)

    got = {(r["kind"], r["scrape_date"], r["player_id"]): (r["position"], r["pos_rank"], r["ecr"]) for r in rows}
    assert got == {
        ("pre", "2023-09-06", people["Wide One"]): ("WR", 2, 1.5),            # 'Nobody Known' holds rank 1
        ("pre", "2023-09-06", people["Wide Two"]): ("WR", 3, 2.0),
        ("pre", "2023-09-06", people["Chiefs D/ST"]): ("DST", 1, 1.0),
        ("ros", "2023-09-15", people["Wide Two"]): ("WR", 1, 1.0),
        ("ros", "2023-09-15", people["Wide One"]): ("WR", 2, 3.0),
        ("ros", "2023-09-29", people["Wide One"]): ("WR", 1, 1.0),
        ("ros", "2023-09-29", people["Wide Two"]): ("WR", 2, 2.0)}


def test_a_player_listed_twice_in_one_snapshot_keeps_his_better_line(db):
    raw = [line("ros-ppr-wr", "2023-09-15", "1", "Wide One", 4.0), line("ros-ppr-wr", "2023-09-15", "1", "W1", 2.0)]

    rows = expert_rows(SEASON, raw, SeasonMatcher(db, SEASON), {"1": "00-W1"}, WEEK1, LAST)

    assert [(r["pos_rank"], r["ecr"]) for r in rows] == [(1, 2.0)]


def test_a_player_on_two_position_pages_in_one_snapshot_is_stored_once(db):
    raw = [line("ros-ppr-te", "2023-09-15", "9", "Some TE", 1.0), line("ros-ppr-te", "2023-09-15", "1", "Wide One", 2.0),
           line("ros-ppr-wr", "2023-09-15", "1", "Wide One", 1.0)]

    rows = expert_rows(SEASON, raw, SeasonMatcher(db, SEASON), {"1": "00-W1"}, WEEK1, LAST)

    assert [(r["position"], r["pos_rank"]) for r in rows] == [("WR", 1)]


def test_a_player_on_two_pages_keeps_his_own_positions_line_even_when_worse(db):
    raw = [line("ros-ppr-te", "2023-09-15", "1", "Wide One", 1.0), line("ros-ppr-wr", "2023-09-15", "2", "Wide Two", 1.0),
           line("ros-ppr-wr", "2023-09-15", "1", "Wide One", 2.0)]

    rows = expert_rows(SEASON, raw, SeasonMatcher(db, SEASON), {"1": "00-W1", "2": "00-W2"}, WEEK1, LAST)

    wide_one = [(r["position"], r["pos_rank"]) for r in rows if r["player_id"] == ids(db)["Wide One"]]
    assert wide_one == [("WR", 2)]                       # not his TE1 line: the pool has him at WR


def rank_rows() -> list[dict]:
    def row(kind, scraped, pid, position, rank):
        return {"kind": kind, "scrape_date": scraped, "player_id": pid, "position": position, "pos_rank": rank}

    return [row("pre", "2023-09-06", 1, "WR", 1), row("pre", "2023-09-06", 2, "WR", 2),
            row("pre", "2023-09-07", 1, "WR", 2), row("pre", "2023-09-07", 2, "WR", 1),    # opener day: too late
            row("pre", "2023-09-01", 3, "K", 1),                               # K page scraped earlier
            row("ros", "2023-09-15", 1, "WR", 2), row("ros", "2023-09-15", 2, "WR", 1),
            row("ros", "2023-09-22", 1, "WR", 1), row("ros", "2023-09-22", 2, "WR", 2),
            row("ros", "2023-09-24", 1, "WR", 9)]                              # scraped on week 3's Sunday


def test_each_week_uses_the_latest_snapshot_before_its_sunday():
    # week 1 opens on Thursday 9-7; week 2's first game is Thursday 9-14; week 3's on Sunday 9-24
    kickoffs = {1: date(2023, 9, 7), 2: date(2023, 9, 14), 3: date(2023, 9, 24), 4: date(2023, 9, 28)}

    ranks, age = week_ranks(rank_rows(), kickoffs)

    assert ranks == {(1, 0): 1, (2, 0): 2, (3, 0): 1,                         # cheatsheets before the Thursday opener
                     (1, 2): 2, (2, 2): 1,                                    # 9-15 is before Sunday 9-17
                     (1, 3): 1, (2, 3): 2,                                    # 9-24 is not before Sunday 9-24
                     (1, 4): 9}                                               # a page with one player
    assert age == {0: 1, 2: 2, 3: 2, 4: 7}


def test_a_player_on_two_pages_with_different_dates_gets_one_rank_whatever_the_row_order():
    def row(scraped, position, rank):
        return {"kind": "ros", "scrape_date": scraped, "player_id": 1, "position": position, "pos_rank": rank}

    rows = [row("2023-09-08", "TE", 3), row("2023-09-01", "QB", 20), row("2023-09-08", "QB", 9),
            row("2023-09-01", "WR", 1)]
    kickoffs = {1: date(2023, 9, 7), 2: date(2023, 9, 14)}

    for order in (rows, rows[::-1]):
        assert week_ranks(order, kickoffs, {1: "QB"})[0][(1, 2)] == 9         # his own (QB) page only
        assert week_ranks(order, kickoffs, {1: "TE"})[0][(1, 2)] == 3
        assert week_ranks(order, kickoffs, {1: "RB"})[0][(1, 2)] == 3         # no own page: newest, then best
        assert week_ranks([r for r in order if r["position"] != "TE"], kickoffs, {1: "RB"})[0][(1, 2)] == 9


def test_no_rows_means_no_ranks():
    assert week_ranks([], {1: date(2023, 9, 7)}) == ({}, {})


def test_seasons_before_2020_are_skipped_without_downloading(db, monkeypatch):
    monkeypatch.setattr(replay_experts, "download", lambda: 1 / 0)

    assert replay_experts.load_expert_ranks(db, 2019) == {"rows": 0, "skipped": "no expert ranks before 2020"}


def test_load_stores_once_then_skips_and_force_downloads_again(db, monkeypatch):
    calls = []
    monkeypatch.setattr(replay_experts, "download", lambda: calls.append(1) or list(RAW))
    monkeypatch.setattr(replay_experts, "fp_crosswalk", lambda: {"1": "00-W1", "2": "00-W2"})

    first = replay_experts.load_expert_ranks(db, SEASON)
    again = replay_experts.load_expert_ranks(db, SEASON)
    forced = replay_experts.load_expert_ranks(db, SEASON, force=True)

    assert first == {"rows": 7, "snapshots": 3} == forced and again == {"rows": 7, "skipped": True}
    assert len(calls) == 1                                  # one download serves the whole prepare run
    replay_experts.clear_cache()
    replay_experts.load_expert_ranks(db, SEASON, force=True)
    assert len(calls) == 2
    assert db.scalar(select(func.count()).select_from(ReplayExpertRank)) == 7


def test_a_failed_download_is_reported_by_type_and_keeps_stored_rows(db, monkeypatch):
    def broken():
        raise ConnectionError("https://example.test/?key=secret")

    monkeypatch.setattr(replay_experts, "download", lambda: list(RAW))
    monkeypatch.setattr(replay_experts, "fp_crosswalk", lambda: {"1": "00-W1"})
    replay_experts.load_expert_ranks(db, SEASON)
    replay_experts.clear_cache()                            # a later prepare run downloads again
    monkeypatch.setattr(replay_experts, "download", broken)

    result = replay_experts.load_expert_ranks(db, SEASON, force=True)

    assert result == {"rows": 7, "errors": {"experts": "ConnectionError"}}
    assert db.scalar(select(func.count()).select_from(ReplayExpertRank)) == 7


def test_a_season_without_dated_games_or_rows_is_reported(db, monkeypatch):
    monkeypatch.setattr(replay_experts, "download", lambda: [])
    monkeypatch.setattr(replay_experts, "fp_crosswalk", dict)

    assert replay_experts.load_expert_ranks(db, 2021) == {"rows": 0, "errors": {"experts": "NoSchedule"}}
    assert replay_experts.load_expert_ranks(db, SEASON) == {"rows": 0, "errors": {"experts": "NoRows"}}



def test_a_failed_import_rolls_back_and_is_reported_by_type(db, monkeypatch):
    monkeypatch.setattr(replay_experts, "download", lambda: list(RAW))
    monkeypatch.setattr(replay_experts, "fp_crosswalk", lambda: {"1": "00-W1"})
    replay_experts.load_expert_ranks(db, SEASON)
    monkeypatch.setattr(replay_experts, "expert_rows", lambda *args: [{"season": SEASON, "player_id": None}])

    result = replay_experts.load_expert_ranks(db, SEASON, force=True)

    assert result == {"rows": 7, "errors": {"experts": "IntegrityError"}}
    assert db.scalar(select(func.count()).select_from(ReplayExpertRank)) == 7         # the delete rolled back
