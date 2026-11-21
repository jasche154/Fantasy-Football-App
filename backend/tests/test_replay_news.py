"""Replay League injury news (services/replay_news.py): noisy, seeded, never naming a week already missed."""
from collections import Counter

import pytest

from services import replay_news as news
from services.replay_news import Report
from tests import replay_fakes as fakes

END = 17
SEEDS = range(400)


def reports(since: int, true_return: int, week: int, tag: str = "O") -> list[Report]:
    return [news.report(seed, 7, since, true_return, week, END, tag) for seed in SEEDS]


@pytest.mark.parametrize("since, true_return", [(3, 4), (3, 5), (5, 9), (2, 14), (6, 0), (16, 17)])
def test_a_report_never_names_a_week_already_missed(since, true_return):
    last = true_return - 1 if true_return else END
    for week in range(since, last + 1):
        assert all(r.back is None or r.back > week for r in reports(since, true_return, week))


def test_the_first_report_misses_by_at_most_two_weeks_with_the_planned_odds():
    misses = Counter(r.back - 10 for r in reports(4, 10, 4) if r.back is not None)

    assert set(misses) <= set(news.ERRORS)
    assert misses[0] == max(misses.values())                     # 35%: the most common
    assert 0.25 < misses[0] / sum(misses.values()) < 0.45 and misses[-1] > misses[2]


def test_the_miss_shrinks_to_nothing_as_he_nears_his_return():
    first = [abs(r.back - 12) for r in reports(4, 12, 4) if r.back is not None]
    near = [abs(r.back - 12) for r in reports(4, 12, 11) if r.back is not None]

    assert max(first) == 2 and max(near) == 0


def test_a_long_absence_is_sometimes_first_called_season_ending_then_corrected():
    early = reports(3, 11, 4)
    called_off = [seed for seed, r in zip(SEEDS, early) if r.back is None]

    assert 0.05 < len(called_off) / len(SEEDS) < 0.16
    for seed in called_off:
        assert news.report(seed, 7, 3, 11, 8, END, "IR").back is None              # still wrong 3 weeks out
        corrected = news.report(seed, 7, 3, 11, 9, END, "IR")                       # 2 weeks before he returns
        assert corrected.back is not None and corrected.back >= 10 and "expected back" in corrected.text


def test_a_short_absence_is_never_called_season_ending():
    assert all(r.back is not None for r in reports(3, 6, 3))


def test_a_season_ending_injury_sometimes_first_names_a_return_then_is_revised():
    first = reports(5, 0, 5)
    hopeful = [(seed, r) for seed, r in zip(SEEDS, first) if r.back is not None]

    assert 0.25 < len(hopeful) / len(SEEDS) < 0.45
    assert all(9 <= r.back <= 13 for _, r in hopeful)                              # 4-8 weeks out
    assert all(news.report(seed, 7, 5, 0, 8, END, "IR").back is None for seed, _ in hopeful)   # revised by 3 weeks
    assert all(r.back is None and "season" in r.text for r in reports(5, 0, 8))


def test_a_suspension_is_reported_exactly():
    assert {r.back for r in reports(2, 6, 2, "SUS")} == {6}
    assert {r.back for r in reports(2, 6, 5, "SUS")} == {6}
    assert {r.back for r in reports(2, 0, 2, "SUS")} == {None}
    assert reports(2, 6, 2, "SUS")[0].text.startswith("Suspended")


def test_the_same_seed_gives_the_same_report_and_seeds_differ():
    assert news.report(3, 7, 4, 10, 5, END, "O") == news.report(3, 7, 4, 10, 5, END, "O")
    assert len({r.back for r in reports(4, 10, 4)}) > 1
    assert news.report(3, 7, 4, 10, 4, END, "O").back == news.report(3, 7, 4, 10, 4, END, "IR").back


def test_share_played_counts_his_teams_games_from_the_reported_week():
    weeks = [6, 7, 8, 10, 11]                                   # a bye in week 9

    assert news.share_played(Report(4, 8, ""), weeks) == pytest.approx(3 / 5)
    assert news.share_played(Report(4, 9, ""), weeks) == pytest.approx(2 / 5)
    assert news.share_played(Report(4, 12, ""), weeks) == 0.0
    assert news.share_played(Report(4, None, ""), weeks) == 0.0
    assert news.share_played(Report(4, 8, ""), []) == 1.0


def test_current_finds_the_report_for_a_hard_tagged_player_only():
    data = fakes.fake_season()
    star = fakes.INJURED_STAR_ID

    assert news.current(data, star, 5, END, 0) is None                       # not out yet
    assert news.current(data, star, 7, END, 0).since == fakes.INJURED_WEEKS[0]
    assert news.current(data, star, fakes.STAR_BACK_WEEK, END, 0) is None    # tagged Q: plays
    assert news.current(data, fakes.BREAKOUT_ID, 7, END, 0) is None          # never tagged
    untold = fakes.tagged(data, {(fakes.BREAKOUT_ID, 7): fakes.Injury("O")})
    assert news.current(untold, fakes.BREAKOUT_ID, 7, END, 0) is None        # no returns entry: no news
