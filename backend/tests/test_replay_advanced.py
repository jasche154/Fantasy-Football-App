"""Replay League advanced stat line: rates from a span's box lines and usage, null on a zero denominator."""
import pytest

from services import replay_advanced as adv
from services.replay_box import BoxLine
from services.replay_pool import Usage

BACK = BoxLine(carries=20, rush_yds=90, rush_td=1, targets=4, rec=3, rec_yds=24, rec_td=1)
BACK_2 = BoxLine(carries=10, rush_yds=40, targets=6, rec=5, rec_yds=36)
PASSER = BoxLine(pass_yds=250, pass_td=2, pass_int=1, carries=3, rush_yds=12)


def test_a_backs_two_games_give_every_rate_from_the_span_totals():
    line = adv.line([BACK, BACK_2], [Usage(70.0, 4, 0.10, 20), Usage(None, 6, None, 10)])

    assert tuple(line) == adv.KEYS
    assert line == {"snap_pct": 70.0, "target_share": 10.0, "targets_pg": 5.0, "carries_pg": 15.0,
                    "touches_pg": 19.0, "ypc": 4.3, "ypr": 7.5, "ypt": 6.0, "catch_rate": 80.0, "total_td": 2,
                    "td_rate": 5.3, "pass_ypg": None, "td_int": None}


def test_per_game_tds_for_last4_and_usage_means():
    line = adv.line([BACK, BACK_2], [Usage(70.0, 4, 0.10, 20), Usage(81.0, 6, 0.25, 10)], per_game_td=True)

    assert (line["total_td"], line["snap_pct"], line["target_share"]) == (1.0, 75.5, 17.5)


def test_zero_denominators_and_missing_usage_are_null():
    line = adv.line([BoxLine()], [])

    assert line["snap_pct"] is line["target_share"] is None
    assert all(line[key] is None for key in ("ypc", "ypr", "ypt", "catch_rate", "td_rate", "pass_ypg", "td_int"))
    assert (line["targets_pg"], line["carries_pg"], line["touches_pg"], line["total_td"]) == (0, 0, 0, 0)


def test_no_games_is_no_line():
    assert adv.line([], [Usage(70.0, 4, 0.1, 20)]) is None


@pytest.mark.parametrize("box, ypg, td_int", [
    (PASSER, 250.0, 2.0), (BoxLine(pass_yds=180, pass_td=3), 180.0, 3), (BoxLine(pass_yds=120, pass_int=2), 120.0, 0),
    (BoxLine(pass_td=1), 0.0, 1), (BoxLine(pass_yds=230, pass_td=2, pass_int=3), 230.0, 0.67),
])
def test_passing_rates(box, ypg, td_int):
    line = adv.line([box], [])

    assert (line["pass_ypg"], line["td_int"]) == (ypg, td_int)


def test_a_quarterbacks_touchdowns_count_his_passes_but_his_td_rate_does_not():
    line = adv.line([PASSER, BoxLine(pass_yds=200, pass_td=1, carries=5, rush_td=1)], [], per_game_td=True)

    assert (line["total_td"], line["td_rate"], line["pass_ypg"], line["td_int"]) == (2.0, 12.5, 225.0, 3.0)
