"""What an absence costs a player's trade chart (services/replay_chart_injury.py, used by replay_chart._injured):
the user's targets, pinned at a mid-season reference of 13 games left."""
import pytest

from services import replay_chart, replay_chart_injury as injury
from services.replay_news import Report
from services.replay_pool import Injury
from tests import replay_fakes as fakes

LEFT = 13


def test_out_one_to_four_weeks_costs_five_to_twelve_percent():
    assert 0.05 <= injury.discount(3, LEFT) <= 0.12
    assert all(0.05 <= injury.discount(missed, LEFT) <= 0.12 for missed in (1, 2, 3, 4))


def test_out_six_to_eight_weeks_costs_35_to_45_percent_when_first_reported():
    assert 0.35 <= injury.discount(8, LEFT) <= 0.45
    assert all(0.35 <= injury.discount(missed, LEFT) <= 0.45 for missed in (6, 7, 8))


def test_the_discount_shrinks_every_week_as_the_return_nears():
    # out 8 of 13 games: each week one game fewer to miss and one fewer left
    path = [injury.discount(8 - week, LEFT - week) for week in range(9)]

    assert all(later < earlier for earlier, later in zip(path, path[1:]))
    assert path[-1] == 0.0


def test_out_for_the_season_is_the_whole_chart_and_a_long_late_absence_still_costs():
    assert injury.discount(LEFT, LEFT) == injury.discount(LEFT + 2, LEFT) == 1.0
    assert injury.discount(0, LEFT) == 0.0
    assert injury.discount(1, LEFT) == 0.06 and injury.discount(1, 1) == 1.0  # one of many games: the user's 6%
    assert injury.discount(4, 5) > 0.25 and injury.discount(2, 3) > 0.15    # most of what is left costs more


CELLS = [(m, left) for left in range(1, 18) for m in range(0, left)]       # every absence short of the season


def test_a_fixed_return_week_never_costs_more_the_next_week():
    assert all(injury.discount(m - 1, left - 1) <= injury.discount(m, left) for m, left in CELLS if m >= 1)


def test_more_games_missed_never_cost_less():
    assert all(injury.discount(m, left) <= injury.discount(m + 1, left) for m, left in CELLS)


def test_the_users_mid_season_targets_stand():
    assert [round(injury.discount(missed, LEFT), 2) for missed in (1, 3, 8)] == [0.06, 0.10, 0.45]


def test_missing_most_of_what_is_left_costs_the_share_to_the_power_1_8_while_trades_are_open():
    """Testers: Kenneth Walker back for the final 2 of 6 games was RB7, Puka Nacua (3 of 6 missed) WR3. Only weeks
    a league can still trade in matter: a default league's deadline week 11 has 7 games left (of a week-17 season),
    a week-12 jump-in trades in weeks 12-13 (6 and 5 left)."""
    deadline = [round(injury.discount(missed, 7), 2) for missed in (4, 5, 6)]
    jump_in = [round(injury.discount(missed, left), 2) for missed, left in ((3, 6), (4, 6), (5, 6), (4, 5))]

    assert deadline == [0.37, 0.55, 0.76]
    assert jump_in == [0.29, 0.48, 0.72, 0.67]
    assert round(injury.discount(1, 2), 2) == 0.29          # after any deadline: priced the same way


def test_a_season_ending_report_keeps_what_such_reports_were_worth():
    keep = [1 - injury.season_report_discount(left) for left in (0, 1, 3, 4, 7, 8, 11, 12, 16)]

    assert keep[0] == 0.0 and 0 < keep[1] < 0.01                           # no game left: nothing
    assert keep == sorted(keep) and 0.05 < keep[-2] < 0.25                 # never zero with games left
    assert keep[-1] == keep[-2] == pytest.approx(injury.SEASON_REPORT_KEEP[-1])


@pytest.mark.parametrize("ir_tag", [False, True])
def test_without_a_report_he_is_never_worth_zero_and_more_games_left_cost_less(ir_tag):
    found = [injury.unreported_discount(left, ir_tag) for left in (1, 2, 3, 4, LEFT)]

    assert all(0 < d < 1 for d in found)                         # only a season-ending report is zero
    assert all(later <= earlier for earlier, later in zip(found, found[1:]))
    assert ir_tag or found[-1] == pytest.approx(sum(odds * injury.discount(LEFT if missed is None else min(missed, LEFT), LEFT)
                                          for missed, odds in injury.NO_REPORT_ODDS) / sum(
        odds for _, odds in injury.NO_REPORT_ODDS))
    assert sum(odds for _, odds in injury.NO_REPORT_ODDS) == pytest.approx(1.0, abs=0.001)


def test_tagged_ir_without_a_report_misses_most_of_the_rest():
    assert injury.unreported_discount(LEFT, True) == pytest.approx(injury.DISCOUNT_ANCHORS[-1][1])
    assert injury.unreported_discount(LEFT, True) > injury.unreported_discount(LEFT, False)


def _star_row(week: int) -> tuple[dict, list[int]]:
    data = fakes.fake_season()
    team = data.team_at(fakes.INJURED_STAR_ID, week)
    games = replay_chart._game_weeks(data, team, week, 17)
    return {"team": team, "games_left": len(games), "out": True, "injury": Injury("O", "DNP", "Ankle")}, games


@pytest.mark.parametrize("back_in", [1, 2])
def test_a_star_newly_out_on_an_o_tag_loses_at_most_twelve_percent(back_in):
    week = 5
    row, games = _star_row(week)
    news = Report(since=week, back=week + back_in, text="")
    expert, model = 600.0, 350.0

    chart, extra = replay_chart._injured(fakes.fake_season(), row, news, week, 17, expert, model)

    assert (expert + model) * 0.88 <= chart < expert + model
    missed = sum(w < week + back_in for w in games)
    assert extra["expected_missed"] == pytest.approx(missed, abs=0.01)       # lineups keep the real games
    assert extra["availability"] == pytest.approx(1 - missed / len(games), abs=0.001)


def test_every_week_out_discounts_the_whole_value_and_a_season_report_keeps_a_little():
    week = 6
    row, games = _star_row(week)
    news = Report(since=week - 1, back=week + 5, text="")
    keep = 1 - injury.discount(sum(w < week + 5 for w in games), len(games))

    chart, _ = replay_chart._injured(fakes.fake_season(), row, news, week, 17, 600.0, 350.0)
    season, extra = replay_chart._injured(fakes.fake_season(), row, Report(week, None, ""), week, 17, 600.0, 350.0)

    assert chart == pytest.approx(950.0 * keep)                     # the frozen value carries no injury twice
    assert season == pytest.approx(950.0 * (1 - injury.season_report_discount(len(games)))) and season > 0
    assert extra["availability"] == 0.0


def test_an_unreported_absence_and_a_healthy_player():
    week = 5
    row, _ = _star_row(week)

    unreported, _ = replay_chart._injured(fakes.fake_season(), row, None, week, 17, 600.0, 350.0)
    healthy, _ = replay_chart._injured(fakes.fake_season(), {**row, "out": False}, None, week, 17, 600.0, 350.0)

    assert unreported == pytest.approx(950.0 * (1 - injury.unreported_discount(row["games_left"], False)))
    assert healthy == 950.0


def test_a_return_after_the_leagues_last_week_reads_like_a_season_ending_report():
    week = 14
    row, games = _star_row(week)
    after_end = Report(since=week, back=18, text="")                    # the NFL plays 18, this league ends in 17

    chart, extra = replay_chart._injured(fakes.fake_season(), row, after_end, week, 17, 600.0, 350.0)
    season, _ = replay_chart._injured(fakes.fake_season(), row, Report(week, None, ""), week, 17, 600.0, 350.0)

    assert chart == season > 0 and extra["availability"] == 0.0
