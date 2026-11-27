"""Replay League box lines: copied from a game line, summed and averaged, and carried in SeasonData."""
from services import replay_box as rb
from services.projections import GameLine
from tests import replay_fakes as fakes
from tests.test_replay_pool import loaded, seeded_db, small_db  # noqa: F401  (fixtures)


def game(**stats) -> GameLine:
    base = dict(week=1, season_type="REG", opponent="BUF", fantasy_points=10.0, expected_points=None,
                expected_tds=None, passing_yards=0, passing_tds=0, interceptions=0, carries=0, rushing_yards=0,
                rushing_tds=0, targets=0, receptions=0, receiving_yards=0, receiving_tds=0, target_share=None,
                snap_pct=None)
    return GameLine(**{**base, **stats})


def test_line_copies_every_box_stat():
    box = rb.line(game(passing_yards=250, passing_tds=2, interceptions=1, carries=4, rushing_yards=21,
                       rushing_tds=1, targets=0, receptions=0, receiving_yards=0, receiving_tds=0, completions=22,
                       attempts=34))

    assert box == rb.BoxLine(22, 34, 250, 2, 1, 4, 21, 1, 0, 0, 0, 0)
    assert rb.KEYS[:5] == ("pass_cmp", "pass_att", "pass_yds", "pass_td", "pass_int")     # box-score order


def test_a_passing_line_without_attempts_shows_null_completions_and_attempts():
    assert rb.shown(rb.BoxLine(pass_yds=250, pass_td=2))["pass_cmp"] is None
    assert rb.shown(rb.BoxLine(pass_int=1))["pass_att"] is None
    assert rb.shown(rb.BoxLine(pass_cmp=20, pass_att=31, pass_yds=250)) | {} == {
        **dict.fromkeys(rb.KEYS, 0), "pass_cmp": 20, "pass_att": 31, "pass_yds": 250}
    assert rb.shown(rb.BoxLine(carries=12))["pass_att"] == 0          # no passing at all: zero, recorded


def test_total_sums_and_mean_averages_to_one_decimal():
    lines = [rb.BoxLine(carries=10, rush_yds=45), rb.BoxLine(carries=11, rush_yds=50, rush_td=1)]

    assert rb.total(lines) == rb.BoxLine(carries=21, rush_yds=95, rush_td=1)
    assert rb.mean(lines) == rb.BoxLine(carries=10.5, rush_yds=47.5, rush_td=0.5)
    assert rb.mean(rb.BoxLine(targets=t) for t in (1, 1, 2)).targets == 1.3


def test_no_games_total_zero_and_mean_none():
    assert rb.total([]) == rb.BoxLine() and rb.mean([]) is None


# ---------------- box lines in SeasonData ----------------

def test_every_skill_game_of_the_fake_season_has_a_box_line_and_load_reads_it_back(loaded):
    data = fakes.fake_season()
    skill = {key for key in data.actual if data.players[key[0]].position in ("QB", "RB", "WR", "TE")}

    assert set(data.box) == skill and dict(loaded.box) == dict(data.box)
    assert loaded.defense_prior == {}                       # the season before isn't loaded


def test_as_of_keeps_box_lines_of_played_weeks_only_and_ignores_the_future():
    data = fakes.fake_season()
    later = fakes.future_changed(data, 6)

    assert dict(data.as_of(7).box) == {key: b for key, b in data.box.items() if key[1] < 7}
    assert later.as_of(7).box == data.as_of(7).box and later.as_of(8).box != data.as_of(8).box


def test_a_real_seasons_box_line_carries_the_databases_completions_and_attempts(small_db):
    from sqlalchemy import update

    from database.models import PlayerStatsWeekly
    from services import replay_pool
    data = fakes.fake_season()
    qb, week = next(key for key in sorted(data.box) if data.players[key[0]].position == "QB")
    small_db.execute(update(PlayerStatsWeekly).where(PlayerStatsWeekly.player_id == qb, PlayerStatsWeekly.week == week)
                     .values(completions=23, attempts=35))
    small_db.commit()
    replay_pool.clear_cache()

    loaded_box = replay_pool.load_season_data(small_db, fakes.SEASON, "half").box

    assert (loaded_box[(qb, week)].pass_cmp, loaded_box[(qb, week)].pass_att) == (23, 35)
    assert loaded_box[(qb, week)] == rb.BoxLine(**{**vars(data.box[(qb, week)]), "pass_cmp": 23, "pass_att": 35})
