"""Replay League box lines: copied from a game line, summed and averaged, and carried in SeasonData."""
from services import replay_box as rb
from services.projections import GameLine
from tests import replay_fakes as fakes
from tests.test_replay_pool import loaded, seeded_db  # noqa: F401  (fixtures)


def game(**stats) -> GameLine:
    base = dict(week=1, season_type="REG", opponent="BUF", fantasy_points=10.0, expected_points=None,
                expected_tds=None, passing_yards=0, passing_tds=0, interceptions=0, carries=0, rushing_yards=0,
                rushing_tds=0, targets=0, receptions=0, receiving_yards=0, receiving_tds=0, target_share=None,
                snap_pct=None)
    return GameLine(**{**base, **stats})


def test_line_copies_every_box_stat():
    box = rb.line(game(passing_yards=250, passing_tds=2, interceptions=1, carries=4, rushing_yards=21,
                       rushing_tds=1, targets=0, receptions=0, receiving_yards=0, receiving_tds=0))

    assert box == rb.BoxLine(250, 2, 1, 4, 21, 1, 0, 0, 0, 0)


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
