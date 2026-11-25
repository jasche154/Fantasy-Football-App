"""Replay League custom scoring (services/replay_scoring.py): the rules, old saves, exact real points from the
box score, the projection estimate (formula and no hindsight) and league creation over HTTP."""
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, select, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, PlayerStatsWeekly, ReplayProjection
from services import replay_pool, replay_scoring, replay_state as rs
from services.replay_box import BoxLine
from services.replay_scoring import ScoringRules
from tests import replay_fakes as fakes
from tests.test_replay_routes import _client

DATA = fakes.fake_season()
API = "/api/replay"
QB, TE, WR = fakes.player_id("QB", 1), fakes.player_id("TE", 1), fakes.player_id("WR", 1)
HALF = ScoringRules(reception=0.5)


# ---------------- rules and settings ----------------

@pytest.mark.parametrize("rules, message", [
    (ScoringRules(reception=0.3), "steps of 0.25"),
    (ScoringRules(reception=1.75), "0 to 1.5"),
    (ScoringRules(te_premium=0.75), "TE premium"),
    (ScoringRules(pass_td=5), "passing TD"),
    (ScoringRules(turnover=-3), "turnover"),
])
def test_rules_outside_the_key_settings_are_refused(rules, message):
    assert message in replay_scoring.problem(rules)
    with pytest.raises(ValueError, match=message):
        fakes.fake_settings(scoring_rules=rules)


def test_rules_equal_to_a_preset_become_that_preset():
    assert replay_scoring.normalize("half", HALF) == ("half", None)
    assert replay_scoring.normalize("half", ScoringRules(reception=1.0)) == ("ppr", None)
    assert replay_scoring.normalize("ppr", None) == ("ppr", None)
    custom = ScoringRules(reception=0.5, pass_td=6.0)
    assert replay_scoring.normalize("half", custom) == ("half", custom)


def test_scoring_settings_put_the_rules_on_every_key_setting():
    rules = replay_scoring.scoring_settings("half", ScoringRules(1.25, 1.0, 6.0, -1.0))

    assert (rules.reception, rules.te_premium, rules.pass_td, rules.interception, rules.fumble_lost) == \
        (1.25, 1.0, 6.0, -1.0, -1.0)
    assert replay_scoring.scoring_settings("half", None).reception == 0.5


def test_an_old_saved_league_loads_unchanged():
    old = ('{"season":2021,"teams":12,"scoring":"half","starters":{"QB":1,"RB":2,"WR":2,"TE":1,"FLEX":1,'
           '"K":1,"DST":1},"bench":6,"draft_slot":1,"waiver_mode":"wednesday","regular_season_weeks":14,'
           '"playoff_teams":4,"trade_deadline_week":11,"season_hidden":false,"difficulty":"normal"}')

    settings = rs.settings_from_json(old)

    assert settings == rs.Settings(season=2021) and settings.scoring_rules is None


def test_custom_rules_and_superflex_survive_json():
    settings = fakes.fake_settings(starters={**rs.DEFAULT_STARTERS, "SUPERFLEX": 1},
                                   scoring_rules=ScoringRules(1.0, 0.5, 6.0, -1.0))

    assert rs.settings_from_json(rs.to_json(settings)) == settings


# ---------------- the projection estimate ----------------

def test_convert_follows_the_documented_formula():
    raw = {(WR, 1): (15.0, 11.0), (TE, 1): (12.0, 8.0), (QB, 1): (20.0, 20.0)}
    positions = {WR: "WR", TE: "TE", QB: "QB"}
    rate_td, rate_int = replay_scoring.QB_TD_PER_POINT, replay_scoring.QB_INT_PER_POINT   # no games yet

    converted = replay_scoring.convert(raw, positions, {}, ScoringRules(1.5, 1.0, 6.0, -1.0))

    assert converted[(WR, 1)] == 11 + 1.5 * 4
    assert converted[(TE, 1)] == 8 + (1.5 + 1.0) * 4
    assert converted[(QB, 1)] == pytest.approx(20 + 2 * rate_td * 20 + 1 * rate_int * 20, abs=0.01)


def test_a_quarterbacks_own_passing_games_move_his_estimate():
    raw = {(QB, 3): (20.0, 20.0)}
    hot = {(QB, w): BoxLine(pass_yds=250, pass_td=4) for w in (1, 2)}
    cold = {(QB, w): BoxLine(pass_yds=250, pass_td=0, pass_int=2) for w in (1, 2)}
    rules = ScoringRules(pass_td=6.0, turnover=-1.0)

    estimates = [replay_scoring.convert(raw, {QB: "QB"}, box, rules)[(QB, 3)] for box in (hot, {}, cold)]

    assert estimates[0] > estimates[1]
    assert estimates[2] != estimates[1]


def test_the_estimate_reads_only_games_before_its_week():
    week = 6
    later = fakes.future_changed(DATA, week)          # box lines (and points) after `week` differ
    raw = {key: (value + 1.0, value - 1.0) for key, value in DATA.projected.items()}
    positions = {pid: p.position for pid, p in DATA.players.items()}
    rules = ScoringRules(reception=1.0, te_premium=0.5, pass_td=6.0, turnover=-1.0)

    before, after = (replay_scoring.convert(raw, positions, data.box, rules) for data in (DATA, later))

    through = {key: value for key, value in before.items() if key[1] <= week + 1}
    assert through == {key: after[key] for key in through}
    assert any(before[key] != after[key] for key in before if key[1] > week + 1 and positions[key[0]] == "QB")


# ---------------- the season from the database ----------------

@pytest.fixture(scope="module")
def db() -> Iterator[Session]:
    """The fake season with one QB game given 2 passing TDs and an interception."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        fakes.seed_db(session, DATA)
        session.execute(update(PlayerStatsWeekly).where(PlayerStatsWeekly.player_id == QB, PlayerStatsWeekly.week == 1)
                        .values(passing_tds=2, passing_interceptions=1))
        session.commit()
        yield session
    engine.dispose()


def test_custom_rules_rescore_real_games_exactly(db):
    load = lambda rules: replay_pool.load_season_data(db, fakes.SEASON, "half", rules)   # noqa: E731
    half, six = load(None), load(ScoringRules(pass_td=6.0))
    soft, premium = load(ScoringRules(turnover=-1.0)), load(ScoringRules(reception=1.5, te_premium=1.0))
    catches = {key: line.rec for key, line in half.box.items()}

    assert six.points(QB, 1) == pytest.approx(half.points(QB, 1) + 2 * 2)          # 2 TDs x 2 more points
    assert soft.points(QB, 1) == pytest.approx(half.points(QB, 1) + 1)             # 1 INT at -1, not -2
    te_game = next(key for key in half.actual if key[0] == TE and catches.get(key))
    wr_game = next(key for key in half.actual if key[0] == WR and catches.get(key))
    assert premium.actual[te_game] == pytest.approx(half.actual[te_game] + 2.0 * catches[te_game])
    assert premium.actual[wr_game] == pytest.approx(half.actual[wr_game] + 1.0 * catches[wr_game])
    assert all(six.actual[key] == half.actual[key] for key in half.actual if key[0] != QB)
    assert (six.rules, half.rules) == (ScoringRules(pass_td=6.0), None) and six is not load(None)


def test_custom_rules_keep_a_projection_whose_preset_column_is_zero(db):
    row = db.scalars(select(ReplayProjection).where(ReplayProjection.source == replay_pool.SOURCES[0],
                                                    ReplayProjection.pts_ppr > 0, ReplayProjection.week == 1)).first()
    half, row.pts_half = row.pts_half, 0.0      # custom rules read pts_ppr / pts_std, never the preset's column
    db.commit()
    try:
        custom = replay_pool._projections(db, fakes.SEASON, "half", 1, custom=True)
    finally:
        row.pts_half = half                     # the database is shared by the module
        db.commit()

    assert custom[(row.player_id, 1)] == (pytest.approx(row.pts_ppr), pytest.approx(row.pts_std or 0.0))


def test_plain_half_is_identical_and_preset_equal_rules_reproduce_the_stored_projections(db):
    plain = replay_pool.load_season_data(db, fakes.SEASON, "half")
    converted = replay_pool.load_season_data(db, fakes.SEASON, "half", HALF)

    assert plain.projected == DATA.projected and plain.rules is None
    assert dict(converted.projected) == pytest.approx(dict(plain.projected))
    assert dict(converted.actual) == pytest.approx(dict(plain.actual))


def test_a_six_point_passing_td_raises_only_quarterback_projections(db):
    plain = replay_pool.load_season_data(db, fakes.SEASON, "half")
    six = replay_pool.load_season_data(db, fakes.SEASON, "half", ScoringRules(reception=0.5, pass_td=6.0))

    for key, value in plain.projected.items():
        if DATA.players[key[0]].position == "QB":
            assert six.projected[key] > value
        else:
            assert six.projected[key] == pytest.approx(value)


# ---------------- creating a league over HTTP ----------------

@pytest.fixture(scope="module")
def client():
    yield from _client(DATA)


def create(client, **body) -> tuple[int, dict]:
    response = client.post(API + "/games", json={"season": fakes.SEASON, "seed": 7, "draft_slot": 3, **body})
    return response.status_code, response.json()


@pytest.mark.parametrize("body, message", [
    ({"starters": {"QB": 0, "RB": 2, "WR": 2}}, "QB or SUPERFLEX"),
    ({"starters": {"QB": 4, "RB": 2}}, "0 to 3"),
    ({"starters": {"QB": 1, "IDP": 1, "RB": 1}}, "Unknown lineup slots"),
    ({"starters": {"QB": 3, "RB": 3, "WR": 3, "TE": 3, "K": 1}}, "at most 12"),
    ({"starters": {"QB": 1, "K": 1}}, "RB, WR, TE or FLEX"),
])
def test_create_refuses_a_bad_lineup_with_a_clear_422(client, body, message):
    status, answer = create(client, **body)

    assert status == 422 and message in answer["detail"]


@pytest.mark.parametrize("field, value", [
    ("reception", 0.3), ("reception", 1.75), ("te_premium", 0.75), ("te_premium", 1.5), ("pass_td", 5),
    ("pass_td", 8), ("turnover", -3), ("turnover", -1.5), ("turnover", 0),
])
def test_the_request_schema_refuses_rules_replay_scoring_does_not_allow(client, field, value):
    status, answer = create(client, scoring_rules={"reception": 1, field: value})

    assert status == 422 and answer["detail"][0]["loc"] == ["body", "scoring_rules", field]
    assert replay_scoring.problem(ScoringRules(**{"reception": 1.0, field: value}))   # the same values, both checks


@pytest.mark.parametrize("rules", [
    {"reception": 0.25, "te_premium": 0.5, "pass_td": 6, "turnover": -1},
    {"reception": 1.5, "te_premium": 1, "pass_td": 4, "turnover": -2},
])
def test_the_request_schema_accepts_every_allowed_value(client, rules):
    assert create(client, scoring_rules=rules)[0] == 200


@pytest.mark.parametrize("body", [{"starters": {"QB": "one"}}, {"scoring_rules": {"reception": 2.0}},
                                  {"scoring_rules": {}}, {"starters": {"QB": 1.5}}])
def test_create_refuses_malformed_values(client, body):
    assert create(client, **body)[0] == 422


def test_create_a_superflex_league_with_custom_scoring(client):
    rules = {"reception": 1.0, "te_premium": 0.5, "pass_td": 6.0, "turnover": -1.0}
    starters = {**rs.DEFAULT_STARTERS, "SUPERFLEX": 1}

    status, answer = create(client, starters=starters, scoring_rules=rules)

    assert status == 200, answer
    settings = answer["game"]["settings"]
    assert settings["starters"] == starters and settings["scoring_rules"] == rules
    assert settings["roster_size"] == 16
    game = f"{API}/games/{answer['game']['id']}"
    assert client.post(game + "/draft/auto", params={"whole_draft": True}).status_code == 200
    rows = client.get(game + "/team").json()["results"]
    flexed = [row for row in rows if row["slot"] == "SUPERFLEX"]
    assert len(flexed) == 1 and flexed[0]["player"]["position"] in ("QB", "RB", "WR", "TE")


def test_create_sets_the_bench_and_refuses_one_out_of_range(client):
    status, answer = create(client, bench=3)

    assert status == 200 and answer["game"]["settings"]["roster_size"] == 9 + 3
    assert create(client, bench=0)[0] == 422 and create(client, bench=11)[0] == 422


def test_preset_equal_rules_make_the_same_league_as_the_preset(client):
    plain = create(client)[1]["game"]
    same = create(client, scoring_rules={"reception": 0.5}, starters=dict(rs.DEFAULT_STARTERS))[1]["game"]

    assert same["settings"] == plain["settings"] and same["settings"]["scoring_rules"] is None
    boards = [client.get(f"{API}/games/{game['id']}/draft").json() for game in (plain, same)]
    assert boards[0]["results"] == boards[1]["results"]


def test_custom_rules_with_another_reception_value_keep_the_chosen_base(client):
    status, answer = create(client, scoring="std", scoring_rules={"reception": 0.25})

    assert status == 200
    assert answer["game"]["settings"]["scoring"] == "std"
    assert answer["game"]["settings"]["scoring_rules"]["reception"] == 0.25


def test_stat_rows_exist_for_the_quarterback_changed_in_the_db(db):
    row = db.scalar(select(PlayerStatsWeekly).where(PlayerStatsWeekly.player_id == QB, PlayerStatsWeekly.week == 1))

    assert (row.passing_tds, row.passing_interceptions) == (2, 1)
