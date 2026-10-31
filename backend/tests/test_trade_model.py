"""Trade model: injured and bye-week values, the verdict's noise band, acceptance ranking, and
league trades scored with roster spots, the deadline and the league-aware calculator."""
from datetime import date
from types import SimpleNamespace

import pytest

from services import league_trades as lm
from services import lineup_gain as lg
from services import trade_finder as tf
from services.league import LeagueSettings
from services.ros_consensus import RosInputs
from services.trade import evaluate_trade, verdict_for
from services.valuation import PlayerValue, expected_vorp, value_players
from tests.test_my_team import league  # noqa: F401  (fixture)
from tests.test_projections_valuation import season_of


def test_injured_player_keeps_value_for_the_games_he_plays():
    # 18 ppg when healthy, plays half the games, replacement 9: 0.5 x (18 - 9) = 4.5 a game, not 0.
    assert expected_vorp(9.0, 0.5, 9.0) == pytest.approx(4.5)
    assert expected_vorp(12.0, 1.0, 9.0) == pytest.approx(3.0)            # healthy: unchanged
    assert expected_vorp(0.2, 0.02, 9.0) == 0.0                            # tiny share: not divided back out


def test_value_players_uses_the_healthy_rate_and_games_left():
    current = [season_of([18] * 4, [18] * 4, "RB", 1), season_of([9] * 4, [9] * 4, "RB", 2),
               season_of([9] * 4, [9] * 4, "RB", 3)]
    league_ = LeagueSettings(teams=1, bench=1, starters={"RB": 1})
    consensus = {1: RosInputs(model=18.0, availability=0.5), 2: RosInputs(model=9.0), 3: RosInputs(model=9.0)}
    values, replacement = value_players(current, [], league_, 8, consensus=consensus)
    hurt = next(v for v in values if v.player_id == 1)
    assert hurt.ros_ppg == 9.0 and replacement["RB"] == 9.0
    assert hurt.trade_value == pytest.approx(36.0)                         # 0.5 x 9 x 8
    with_bye, _ = value_players(current, [], league_, 8, consensus=consensus, games_left={1: 7})
    assert next(v for v in with_bye if v.player_id == 1).trade_value == pytest.approx(31.5)
    assert next(v for v in with_bye if v.player_id == 1).games_left == 7


def test_small_point_gaps_are_fair_whatever_the_percent():
    assert verdict_for(-0.33) == "big_loss"                                # no horizon: percent only
    assert verdict_for(-0.33, -1.0, 9) == "fair"                           # 2 vs 3 points over 9 weeks: noise
    assert verdict_for(-0.33, -8.0, 9) == "slight_loss"                    # under 1.2 a week
    assert verdict_for(-0.33, -30.0, 9) == "big_loss"
    assert verdict_for(0.04, 20.0, 9) == "fair"                            # within 5% stays fair


def _value(pid: int, trade_value: float, **fields) -> PlayerValue:
    return PlayerValue(player_id=pid, name=f"P{pid}", position="WR", team="KC", games=6, ppg=10.0, expected_ppg=None,
                       ros_ppg=10.0, vorp_per_game=trade_value / 9, trade_value=trade_value, **fields)


def test_evaluate_trade_reports_the_noise_band():
    give, get = _value(1, 3.0), _value(2, 2.0)                             # 33% apart, 1 point over 9 weeks
    result = evaluate_trade([give], [get], [give, get, _value(3, 1.0)], 9)
    assert result.verdict == "fair" and result.balance_suggestions == ()


def test_market_value_uses_known_games_left():
    valuation = SimpleNamespace(replacement={"WR": 8.0}, weeks_remaining=9)
    bye_out = _value(1, 0.0, expert_ros_ppg=12.0, games_left=0)
    unknown = _value(2, 0.0, expert_ros_ppg=12.0)
    assert tf.market_value(bye_out, valuation).trade_value == 0.0           # 0 games really left
    assert tf.market_value(unknown, valuation).trade_value == pytest.approx(36.0)   # not in the schedule


def test_accept_chance_rises_as_the_market_favours_them():
    assert tf.accept_chance(0.0) == pytest.approx(0.622, abs=0.001)
    assert tf.accept_chance(0.05) < tf.accept_chance(0.0) < tf.accept_chance(-0.10)
    assert tf.accept_chance(0.5) < 0.01 and tf.accept_chance(-0.5) > 0.99


def test_offers_rank_by_expected_edge_then_acceptance():
    target = SimpleNamespace(ros_ppg=10.0)
    likely_small = tf._rank_key(0.05, tf.accept_chance(-0.05), target)   # they think they win 5%
    unlikely_big = tf._rank_key(0.08, tf.accept_chance(0.05), target)    # they think they lose 5%
    no_edge = tf._rank_key(-0.02, 0.9, target)
    assert sorted([no_edge, unlikely_big, likely_small]) == [likely_small, unlikely_big, no_edge]


def test_backup_qb_keeps_his_small_share_in_lineup_gain():
    doubtful = SimpleNamespace(status="DOUBTFUL")
    projector = lg.Projector(None, {4: doubtful}, {}, 8)
    backup = SimpleNamespace(player_id=1, position="QB", availability=0.1, ros_ppg=1.6)
    starter = SimpleNamespace(player_id=2, position="QB", availability=1.0, ros_ppg=20.0)
    deep_backup = SimpleNamespace(player_id=3, position="QB", availability=0.03, ros_ppg=0.5)
    hurt = SimpleNamespace(player_id=4, position="WR", availability=0.67, ros_ppg=10.0)
    benched = SimpleNamespace(player_id=5, position="WR", availability=0.0, ros_ppg=0.0)
    assert projector.available(backup, 2) == pytest.approx(0.1)
    assert projector.available(starter, 2) == pytest.approx(lg.healthy_share("QB"))
    assert projector.healthy_rate(backup) * projector.available(backup, 2) == pytest.approx(1.6)
    # Tiny share: the rate wasn't divided back out, so it isn't discounted again.
    assert projector.healthy_rate(deep_backup) * projector.available(deep_backup, 2) == pytest.approx(
        0.5 * lg.healthy_share("QB"))
    # Doubtful now: this week is in the weekly projection, so later weeks are healthy, not x 0.67 again.
    assert projector.available(hurt, 2) == pytest.approx(lg.healthy_share("WR"))
    assert projector.healthy_rate(benched) == 0.0
    # A Questionable backup QB still plays only the games his starter misses, not like a starter.
    tagged = lg.Projector(None, {6: SimpleNamespace(status="QUESTIONABLE")}, {}, 8)
    q_backup = SimpleNamespace(player_id=6, position="QB", availability=0.2, role_share=0.2, ros_ppg=3.0)
    assert tagged.available(q_backup, 2) == pytest.approx(0.2)
    assert tagged.healthy_rate(q_backup) * tagged.available(q_backup, 2) == pytest.approx(3.0)


def test_offer_score_penalizes_offers_that_hurt_them():
    assert lm.offer_score(22, 10) > lm.offer_score(30, -40)
    assert lm.offer_score(10, 5) == 10


def test_distinct_caps_offers_built_on_one_player():
    star = SimpleNamespace(player_id=1)
    offers = [(SimpleNamespace(give=(star,)), 9 - i, 0) for i in range(4)]
    assert len(lm._distinct(offers)) == lm.MAX_SHARED_GIVE


def test_forced_drop_removes_the_player_missed_least(session, league):  # noqa: F811
    from services import my_team as mt

    row, _ = league
    team = mt.build_my_team(session, row.id)
    horizon, projector = lg.context_for(team)
    roster = lg.roster_values(team)
    kept, dropped = lm._forced_drop(roster, horizon, projector, team.settings, True)
    assert dropped is not None and len(kept) == len(roster) - 1
    best_after = lg.season_points(kept, horizon, projector, team.settings)
    for other in roster:
        alt = [v for v in roster if v.player_id != other.player_id]
        if other in sorted(roster, key=lambda v: v.ros_ppg)[:lm.DROP_CANDIDATES]:
            assert best_after >= lg.season_points(alt, horizon, projector, team.settings) - 1e-9
    assert lm._forced_drop(roster, horizon, projector, team.settings, False) == (roster, None)


def test_league_trades_score_both_lineups_with_named_extras(client, league):  # noqa: F811
    row, _ = league
    body = client.get(f"/api/my-team/{row.id}/trades").json()
    assert body["deadline_passed"] is False
    for team in body["teams"]:
        assert len(team["offers"]) <= 3
        for offer in team["offers"]:
            assert offer["your_gain"] > 0 and "helps_both" in offer and "accept_chance" in offer
            assert offer["helps_both"] == (offer["their_gain"] > 0)
            assert "their_drop" in offer and "your_add" in offer


def test_trade_deadline_closes_league_offers(client, session, league):  # noqa: F811
    assert lm.deadline_passed("2026-11-20", today=date(2026, 11, 20)) is False   # trades allowed that day
    assert lm.deadline_passed("2026-11-20", today=date(2026, 11, 21)) is True
    assert lm.deadline_passed(None) is False and lm.deadline_passed("not a date") is False
    row, _ = league
    import json

    from services import my_team as mt

    row.scoring_settings = json.dumps({**mt.league_rules(row), "trade_end_date": "2000-01-01"})
    session.commit()
    body = client.get(f"/api/my-team/{row.id}/trades").json()
    assert body["deadline_passed"] is True and body["teams"] == []


def test_uneven_trades_count_roster_spots(session, league):  # noqa: F811
    from services import my_team as mt

    row, ids = league
    scorer = lm.TradeScorer(session, row.id)
    by_id = scorer.team.valuation.by_id
    mine = [by_id[ids[n]] for n in ("WR Player 05", "RB Player 01")]
    theirs = by_id[ids["WR Player 02"]]
    rival = next(t for t in scorer.rosters if t != scorer.team.team.id)
    one_for_one = scorer.score(mine[:1], [theirs], rival)
    assert one_for_one["their_drop"] is None and one_for_one["my_add"] is None
    scorer.capacity = lm.active_spots(scorer.rosters[rival])                # their roster is full
    scorer.my_full = True
    two_for_one = scorer.score(mine, [theirs], rival)
    assert two_for_one["their_drop"] is not None                            # they must drop someone
    assert scorer.score(mine[:1], [theirs], rival)["their_drop"] is None    # never on a 1-for-1
    free = scorer.free_agents()
    if free:
        assert two_for_one["my_add"] is None or two_for_one["my_add"].player_id in {v.player_id for v in free}
    scorer._free_agents = []
    assert scorer._open_spot_add(scorer.mine, scorer.my_base) == (0.0, None)
    assert mt.OFFERS_PER_TEAM == 3
    scorer.capacity = lm.active_spots(scorer.team.spots)                    # your roster is full too
    one_for_two = scorer.score(mine[:1], [theirs, by_id[ids["RB Player 03"]]], rival)
    assert one_for_two["my_drop"] is not None                               # you drop someone to fit two


def test_calculator_scores_a_trade_on_your_league(client, league):  # noqa: F811
    row, ids = league
    give, get = ids["WR Player 01"], ids["WR Player 02"]
    body = client.post("/api/trade/evaluate", json={"give": [give], "get": [get], "league_id": row.id}).json()
    assert body["lineup"]["their_team"] == "Rival"
    assert isinstance(body["lineup"]["your_gain"], float) and isinstance(body["lineup"]["their_gain"], float)
    assert body["market_percent_difference"] is not None
    assert {"pos_rank", "games_left", "availability"} <= set(body["give"]["players"][0])
    mine_and_theirs = {ids[n] for n in ("QB Player 01", "RB Player 20", "RB Player 01", "WR Player 01", "TE Player 01",
                                        "WR Player 05", "RB Player 05", "QB Player 02", "RB Player 02", "WR Player 02",
                                        "TE Player 02", "WR Player 03", "RB Player 03")}
    assert all(s["player"]["id"] in mine_and_theirs for s in body["balance_suggestions"])
    plain = client.post("/api/trade/evaluate", json={"give": [give], "get": [get]}).json()
    assert plain["lineup"] is None


def test_calculator_league_errors(client, league):  # noqa: F811
    row, ids = league
    not_mine = client.post("/api/trade/evaluate", json={"give": [ids["WR Player 02"]], "get": [ids["WR Player 03"]],
                                                         "league_id": row.id})
    assert not_mine.status_code == 200                                      # the value verdict still stands
    assert not_mine.json()["verdict"] and "aren't on your roster" in not_mine.json()["lineup"]["note"]
    assert not_mine.json()["lineup"]["your_gain"] is None
    own = client.post("/api/trade/evaluate", json={"give": [ids["WR Player 01"]], "get": [ids["WR Player 05"]],
                                                   "league_id": row.id})
    assert "already on your roster" in own.json()["lineup"]["note"]
    missing = client.post("/api/trade/evaluate", json={"give": [ids["WR Player 01"]], "get": [ids["WR Player 02"]],
                                                       "league_id": 9999})
    assert missing.status_code == 404
