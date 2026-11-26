"""The trade chart check's target (services/replay_chart_eval.py): one synthetic case per rule, and the
leave-season-out calibration never seeing the season it scores."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart_eval as ev, replay_values as rv
from services.replay_pool import Injury, SeasonData
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
PID = fakes.player_id("WR", 2)
END = 17
WEEK = 5
LEVEL = {"QB": 0.0, "RB": 0.0, "WR": 2.0, "TE": 0.0}
TEAM_WEEKS = [w for w in range(1, END + 1) if DATA.schedule.opponent(DATA.team_at(PID, w), w) is not None]


def season(played: dict[int, float], tags: dict[int, str] | None = None) -> SeasonData:
    """DATA with this player's real games and tags replaced (his team, and so his byes, unchanged)."""
    actual = {key: v for key, v in DATA.actual.items() if key[0] != PID}
    actual.update({(PID, week): points for week, points in played.items()})
    injuries = {key: v for key, v in DATA.injuries.items() if key[0] != PID}
    injuries.update({(PID, week): Injury(tag, None, "Knee") for week, tag in (tags or {}).items()})
    return replace(DATA, actual=MappingProxyType(actual), injuries=MappingProxyType(injuries))


def every_game(points: float = 12.0, skip: tuple[int, ...] = ()) -> dict[int, float]:
    return {week: points + week for week in TEAM_WEEKS if week not in skip}


def target(data: SeasonData, out: bool = False) -> tuple[float, str] | None:
    return ev.target(data, PID, WEEK, END, out, LEVEL)


def test_a_healthy_player_is_scored_on_every_game_from_the_week_on():
    played = every_game()
    later = [played[w] for w in TEAM_WEEKS if w >= WEEK]

    assert target(season(played)) == (pytest.approx(sum(later) / len(later) - 2.0), "healthy")
    assert DATA.schedule.opponent(DATA.team_at(PID, 1), 1) is not None and len(TEAM_WEEKS) < END   # a bye week


def test_games_lost_to_a_later_injury_are_left_out():
    hurt = [w for w in TEAM_WEEKS if w >= 8][:3]
    played = every_game(skip=tuple(hurt))
    later = [played[w] for w in TEAM_WEEKS if w >= WEEK and w not in hurt]

    value, rule = target(season(played, {hurt[0]: "O"}))         # one tag opens the streak to his next game

    assert (value, rule) == (pytest.approx(sum(later) / len(later) - 2.0), "healthy_later_injury")


def test_a_later_benching_counts_as_zero_points():
    benched = [w for w in TEAM_WEEKS if w >= 8][:3]
    played = every_game(skip=tuple(benched))
    later = [played.get(w, 0.0) for w in TEAM_WEEKS if w >= WEEK]

    value, rule = target(season(played))

    assert (value, rule) == (pytest.approx(sum(later) / len(later) - 2.0), "healthy_zero_games")
    assert value < target(season(every_game()))[0]


def test_an_absence_known_at_the_week_costs_its_share_of_the_games():
    out = [w for w in TEAM_WEEKS if w >= WEEK][:3]
    played = every_game(skip=tuple(out))
    left = [w for w in TEAM_WEEKS if w >= WEEK]
    after = [played[w] for w in left if w not in out]

    value, rule = target(season(played, {w: "O" for w in out}), out=True)

    assert rule == "out_back"
    assert value == pytest.approx((sum(after) / len(after) - 2.0) * (1 - len(out) / len(left)))


def test_out_for_the_season_is_worth_nothing():
    played = {w: p for w, p in every_game().items() if w < WEEK}

    assert target(season(played, {WEEK: "IR"}), out=True) == (0.0, "out_season")


def test_back_for_one_game_is_scored_with_his_rate_before_the_absence_too():
    last = TEAM_WEEKS[-1]
    played = {w: p for w, p in every_game().items() if w < WEEK or w == last}
    games = [played[w] for w in TEAM_WEEKS if w < WEEK] + [played[last]]
    left = [w for w in TEAM_WEEKS if w >= WEEK]

    value, rule = target(season(played, {WEEK: "IR"}), out=True)

    assert rule == "out_back_short"
    assert value == pytest.approx((sum(games) / len(games) - 2.0) * (1 / len(left)))


def test_too_few_games_is_not_scored_and_replacement_reads_everyone():
    two_left = [w for w in TEAM_WEEKS if w >= WEEK][-2]

    assert ev.target(season(every_game()), PID, two_left, END, False, LEVEL) is None     # 2 games: no sample
    found = ev.realised(season(every_game()), WEEK, END, {PID: False}, rv.league_settings(fakes.fake_settings()))
    assert found[PID][1] == "healthy"


def row(season_: int, target_: float, position: str = "WR") -> ev.Row:
    return ev.Row(season_, 1, "regular", 1, "P", position, 500.0, 500.0, False, target_, "healthy", 1)


def test_the_held_out_curve_never_sees_the_season_it_scores():
    rows = [row(2020, 10.0), row(2020, 10.0), row(2021, 0.0), row(2021, 0.0)]

    found = ev.held_out_error(rows)

    assert found["rmse"] == pytest.approx(10.0)                 # a leak would predict 5 and miss by 5
    assert found["bias_WR"] == pytest.approx(0.0)               # +10 in 2020, -10 in 2021
