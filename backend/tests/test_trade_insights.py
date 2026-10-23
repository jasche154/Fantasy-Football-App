import pytest

from services.insights import compute_signals, find_insights, player_signals
from services.league import LeagueSettings
from services.trade import (
    depth_weight, evaluate_trade, package, percent_difference, summarize, verdict_for,
)
from services.valuation import PlayerValue, build_valuation


def value(pid: int, trade_value: float, position: str = "WR") -> PlayerValue:
    return PlayerValue(player_id=pid, name=f"P{pid}", position=position, team="AAA", games=6, ppg=15.0,
                       expected_ppg=15.0, ros_ppg=15.0, vorp_per_game=trade_value / 10, trade_value=trade_value)


def test_depth_weights_step_down_with_floor():
    assert [depth_weight(i) for i in range(7)] == [1.0, 0.9, 0.8, 0.7, 0.6, 0.5, 0.5]


def test_package_sorts_by_value_before_discounting():
    side = package([value(1, 50), value(2, 100)])
    assert [p.value.player_id for p in side.players] == [2, 1]
    assert (side.raw_total, side.adjusted_total) == (150, 145)


def test_two_mid_players_do_not_equal_one_stud():
    result = evaluate_trade([value(1, 100)], [value(2, 50), value(3, 50)], [], 10)
    assert result.get.raw_total == 100 and result.get.adjusted_total == 95
    assert result.verdict == "fair" and result.difference == -5


@pytest.mark.parametrize("percent, verdict", [
    (0.0, "fair"), (0.05, "fair"), (-0.05, "fair"), (0.1, "slight_win"), (-0.15, "slight_loss"),
    (0.2, "big_win"), (-0.4, "big_loss"),
])
def test_verdict_thresholds(percent, verdict):
    assert verdict_for(percent) == verdict


def test_percent_difference_handles_zero_value_sides():
    assert percent_difference(0, 0) == 0.0
    assert percent_difference(100, 50) == -0.5


def test_summary_sentences():
    assert summarize("fair", 1.0, 0.01, 11).startswith("Fair trade")
    assert "You win" in summarize("big_win", 30.0, 0.3, 11) and "clear edge" in summarize("big_win", 30, 0.3, 11)
    assert "You lose" in summarize("slight_loss", -8.0, -0.08, 11)


def test_balance_suggestions_target_the_short_side():
    pool = [value(10, 40), value(11, 60), value(12, 5), value(13, 0)]
    losing = evaluate_trade([value(1, 100)], [value(2, 50)], pool, 10)
    assert losing.verdict == "big_loss"
    assert losing.balance_suggestions and all(s.add_to == "get" for s in losing.balance_suggestions)
    assert losing.balance_suggestions[0].value.player_id == 11      # 50 + 0.9*60 lands closest to 100
    assert all(s.value.trade_value > 0 for s in losing.balance_suggestions)
    winning = evaluate_trade([value(2, 50)], [value(1, 100)], pool, 10)
    assert all(s.add_to == "give" for s in winning.balance_suggestions)
    fair = evaluate_trade([value(1, 100)], [value(2, 99)], pool, 10)
    assert fair.balance_suggestions == ()


def test_trade_validation():
    with pytest.raises(ValueError, match="at least one"):
        evaluate_trade([], [value(1, 10)], [], 10)
    with pytest.raises(ValueError, match="both sides"):
        evaluate_trade([value(1, 10)], [value(1, 10)], [], 10)
    with pytest.raises(ValueError, match="more than once"):
        evaluate_trade([value(1, 10)], [value(2, 10), value(2, 10)], [], 10)


def test_buy_low_and_sell_high_find_the_seeded_profiles(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    buys = find_insights(valuation, "buy_low")
    sells = find_insights(valuation, "sell_high")
    assert buys[0].player_id == seeded["buy"]
    assert sells[0].player_id == seeded["sell"]
    buy = buys[0]
    assert buy.fpoe_per_game == -8.0 and buy.usage_trend == 1.0 and buy.td_diff == -6.0
    assert any("fewer PPR pts/game than usage predicts" in r for r in buy.reasons)
    assert any("Snap share up" in r for r in buy.reasons)
    assert any("6 TDs vs 2.4 expected" in r for r in sells[0].reasons)
    assert all(i.trade_value > 0 for i in (*buys, *sells))


def test_insight_filters_and_validation(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    assert find_insights(valuation, "buy_low", position="QB") == []
    assert len(find_insights(valuation, "sell_high", limit=1)) <= 1
    with pytest.raises(ValueError):
        find_insights(valuation, "hold")


def test_player_signals_for_profile(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    season = next(p for p in valuation.seasons if p.player_id == seeded["sell"])
    signals = player_signals(season, valuation)
    assert signals["sell_high"] and not signals["buy_low"]
    neutral = next(p for p in valuation.seasons if p.player_id == seeded["chase"])
    assert player_signals(neutral, valuation)["reasons"][0].startswith("Production matches usage")


def test_signals_need_minimum_games(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    season = next(p for p in valuation.seasons if p.player_id == seeded["buy"])
    short = type(season)(season.player_id, season.name, season.position, season.team, season.season,
                         season.games[:2])
    assert compute_signals(short) is None
    assert "Needs 3+ games" in player_signals(short, valuation)["reasons"][0]


def test_signals_survive_a_missing_expected_points_week(session, seeded):
    from sqlalchemy import delete

    from database.models import PlayerExpectedPointsWeekly

    session.execute(delete(PlayerExpectedPointsWeekly).where(PlayerExpectedPointsWeekly.week == 6))
    session.commit()
    valuation = build_valuation(session, LeagueSettings())
    buys = find_insights(valuation, "buy_low")
    assert buys and buys[0].player_id == seeded["buy"]
    # Weeks 1-5 only: 12 actual vs (3 x 19 + 2 x 21) / 5 = 19.8 expected.
    assert buys[0].fpoe_per_game == -7.8

