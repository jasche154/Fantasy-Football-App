"""An injured player's trade chart over his absence (replay_values._frozen + replay_chart_injury): the value he
had the week before falls once by the user's curve and climbs back to it the week he returns."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_news, replay_values as rv
from services.replay_pool import Injury, hard_tag_returns
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
SETTINGS = fakes.fake_settings()
END = SETTINGS.end_week
LEAGUE = rv.league_settings(SETTINGS)
PID = fakes.player_id("WR", 2)
GAMES = [w for w in range(1, END + 1) if DATA.schedule.opponent(DATA.team_at(PID, w), w) is not None]
START = next(w for w in GAMES if sum(g >= w for g in GAMES) == 13)        # 13 of his team's games left


def hurt(games: int):
    """DATA with PID out (O tag, no projection, no game) for his team's next `games` games from START, and a
    news draw whose report names his real return."""
    out = set(GAMES[GAMES.index(START):GAMES.index(START) + games])
    gone = {(PID, w) for w in out}
    projected = {k: v for k, v in DATA.projected.items() if k not in gone}
    actual = {k: v for k, v in DATA.actual.items() if k not in gone}
    injuries = {**DATA.injuries, **{key: Injury("O", "DNP", "Ankle") for key in gone}}
    data = replace(DATA, projected=MappingProxyType(projected), actual=MappingProxyType(actual),
                   injuries=MappingProxyType(injuries),
                   returns=MappingProxyType(hard_tag_returns(injuries, actual, DATA.last_week)))
    back = data.returns[(PID, START)]
    seed = next(s for s in range(500) if all(
        replay_news.report(s, PID, START, back, w, "O", data.last_week).back == back for w in range(START, back)))
    return data, seed, back


def value(data, week: int, seed: int) -> rv.ReplayValue:
    return rv.knowledge(data, LEAGUE, week, END, seed).by_id[PID]


def loss(data, week: int, seed: int, before: float) -> float:
    return 1 - value(data, week, seed).chart / before


def test_the_player_is_a_healthy_starter_before():
    assert not any(tag.hard for (pid, _), tag in DATA.injuries.items() if pid == PID)
    assert value(DATA, START - 1, 0).healthy_chart > 300


def test_one_game_out_costs_five_to_eight_percent_and_he_is_back_at_his_value_the_next_week():
    data, seed, back = hurt(1)
    before = value(data, START - 1, seed).healthy_chart

    assert 0.05 <= loss(data, START, seed, before) <= 0.08
    assert value(data, back, seed).chart == pytest.approx(before)          # before his first game back


def test_three_of_thirteen_cost_five_to_twelve_percent():
    data, seed, _ = hurt(3)
    before = value(data, START - 1, seed).healthy_chart

    assert 0.05 <= loss(data, START, seed, before) <= 0.12


def test_eight_of_thirteen_cost_35_to_45_percent_then_less_every_week_and_nothing_on_return():
    data, seed, back = hurt(8)
    before = value(data, START - 1, seed).healthy_chart
    weeks = [w for w in range(START, back) if w in GAMES]
    losses = [loss(data, w, seed, before) for w in weeks]

    assert 0.35 <= losses[0] <= 0.45
    assert all(later < earlier for earlier, later in zip(losses, losses[1:]))
    assert value(data, weeks[-1], seed).healthy_chart == pytest.approx(before)     # frozen all along
    assert abs(loss(data, back, seed, before)) <= 0.05                              # the week he returns


def test_the_frozen_value_sees_no_hindsight():
    data, seed, back = hurt(8)
    week = START + 2

    assert value(data, week, seed) == value(fakes.future_changed(data, week), week, seed)
