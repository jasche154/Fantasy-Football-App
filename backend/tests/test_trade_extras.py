"""Trade verdict extras (consolidation, add-to-even), lineup labels, waiver presentation, the
points-vs-values invariant across league formats, and the replacement-level evaluation."""
import math

import pytest
from sqlalchemy import insert

from database.models import PlayerExpectedPointsWeekly, PlayerStatsWeekly
from services import serialize
from services import waiver_advice as wa
from services.league import LeagueSettings
from services.league_trades import LABEL_DEAD_BAND, lineup_label, trade_impact
from services.trade import adjusted_total, evaluate_trade
from services.valuation import PlayerValue, build_valuation
from tests.conftest import CURRENT, CURRENT_WEEKS, _add_player, expected_row, stat_row
from tests.test_my_team import league  # noqa: F401  (fixture)


def _pv(pid: int, value: float, position: str = "WR") -> PlayerValue:
    return PlayerValue(player_id=pid, name=f"P{pid}", position=position, team="T", games=6, ppg=10.0,
                       expected_ppg=None, ros_ppg=10.0, vorp_per_game=value / 10, trade_value=value)


def test_two_for_one_reports_the_consolidation_and_what_evens_it():
    pool = [_pv(10, 17.0), _pv(11, 60.0), _pv(12, 5.0)]
    result = evaluate_trade([_pv(1, 100.0), _pv(2, 60.0)], [_pv(3, 130.0)], pool, weeks_remaining=10)
    assert result.verdict == "slight_loss" and result.difference == -12.0
    # Raw gap -30, adjusted -12: the 0.7 weight on the second player moves it 18 points toward the 1-player side.
    assert result.consolidation.side == "you" and result.consolidation.points == 18.0
    assert "2-for-1" in result.consolidation.note and "toward you" in result.consolidation.note
    # 130 + 0.7 v = 142 -> v = 17.1 added to what you get.
    assert result.even_it.add_to == "get" and result.even_it.value == pytest.approx(17.1, abs=0.05)
    body = serialize.trade(result, 10)
    assert body["consolidation"] == {"side": "you", "points": 18.0, "note": result.consolidation.note}
    assert body["even_it"]["closest"]["player"]["id"] == 10          # the suggestion that comes closest


def test_even_it_handles_a_new_best_player_and_no_extras_on_fair_or_even_counts():
    # Sides 50 vs 200: the added player becomes the short side's best (50 drops to 70% weight).
    result = evaluate_trade([_pv(1, 200.0)], [_pv(2, 50.0)], [], weeks_remaining=10)
    assert result.consolidation is None                                  # 1-for-1
    assert adjusted_total([50.0, result.even_it.value]) == pytest.approx(200.0, abs=0.1)
    assert result.even_it.value == pytest.approx(165.0, abs=0.1)
    assert serialize.trade(result, 10)["even_it"]["closest"] is None    # no pool, no player to name
    fair = evaluate_trade([_pv(1, 50.0)], [_pv(2, 51.0)], [], weeks_remaining=10)
    assert fair.verdict == "fair" and fair.even_it is None and serialize.trade(fair, 10)["even_it"] is None


def test_even_it_uses_the_same_rounded_totals_as_the_verdict():
    give, get = [_pv(1, 97.3), _pv(2, 41.7), _pv(3, 13.3)], [_pv(4, 88.8)]
    result = evaluate_trade(give, get, [], weeks_remaining=10)
    filler = _pv(9, result.even_it.value)
    evened = evaluate_trade(give, [*get, filler], [], weeks_remaining=10)
    assert evened.difference == 0.0                                       # exactly even, as the verdict sees it
    smaller = evaluate_trade(give, [*get, _pv(9, result.even_it.value - 0.2)], [], weeks_remaining=10)
    assert smaller.difference < 0


def test_they_consolidate_when_you_get_more_players():
    result = evaluate_trade([_pv(1, 130.0)], [_pv(2, 100.0), _pv(3, 60.0)], [], weeks_remaining=10)
    assert result.consolidation.side == "them" and result.consolidation.points == -18.0
    assert "They get" in result.consolidation.note and "toward them" in result.consolidation.note


@pytest.mark.parametrize(("mine", "theirs", "label"), [
    (3.0, 2.0, "Helps both lineups"), (3.0, -2.0, "Helps you, costs them"),
    (-3.0, 2.0, "Helps them, costs you"), (-3.0, -2.0, "Hurts both"),
    (3.0, LABEL_DEAD_BAND, "Helps you, even for them"), (0.2, -0.4, "About even for both"),
    (-1.0, 0.0, "Costs you, even for them"), (2.0, None, "Helps your lineup"), (None, None, None),
])
def test_lineup_labels_follow_both_gains_with_a_dead_band(mine, theirs, label):
    assert lineup_label(mine, theirs) == label


def test_league_trades_and_the_calculator_carry_the_label(client, session, league):
    row, ids = league
    trades = client.get(f"/api/my-team/{row.id}/trades").json()
    for team in trades["teams"]:
        for offer in team["offers"]:
            assert offer["lineup_label"] == lineup_label(offer["your_gain"], offer["their_gain"])
    impact = trade_impact(session, row.id, [ids["WR Player 05"]], [ids["WR Player 02"]])
    assert impact["lineup_label"] == lineup_label(impact["your_gain"], impact["their_gain"])
    body = client.post("/api/trade/evaluate", json={"give": [ids["WR Player 05"]], "get": [ids["WR Player 02"]],
                                                    "league_id": row.id}).json()
    assert body["lineup"]["lineup_label"] == impact["lineup_label"]
    assert "consolidation" in body and "even_it" in body


def test_faab_bids_show_the_share_of_the_original_budget():
    advice = wa.faab_advice(gain=20, rank=1, position="RB", balance=50, budget=200, weeks_left=8,
                            rivals=[], winning_bids_by_week=[], week=9)
    assert advice.action == "bid" and advice.budget_pct == round(advice.bid / 200 * 100)
    assert f"({advice.budget_pct}% of budget)" in advice.note
    assert wa.faab_advice(0.5, 1, "RB", 50, 200, 8, [], [], 9).budget_pct is None      # pass: no bid


def test_priority_claims_say_which_priority_they_are_worth():
    assert wa.worth_priority(True, 9) == 1                                # a difference-maker: any priority
    assert wa.worth_priority(False, 1) == wa.TOP_PRIORITY + 1             # top 3 kept for difference-makers
    assert wa.worth_priority(False, 7) == 7 and wa.worth_priority(False, None) == wa.TOP_PRIORITY + 1
    seventh = wa.priority_advice(3, 2, "RB", 5, [], False, 1, gain_rank=7)
    assert seventh.action == "wait" and seventh.worth_priority == 7 and "worth #7 or later" in seventh.note
    late = wa.priority_advice(3, 2, "RB", 8, [], False, 1, gain_rank=7)
    assert late.action == "claim" and late.worth_priority == 7
    starter = wa.priority_advice(3, 2, "RB", 1, [], False, 3, gain_rank=7)
    assert starter.action == "claim" and starter.worth_priority == 1
    # Never above the team count, and the last priority is always free to use.
    assert wa.worth_priority(False, 40, teams=12) == 12
    last = wa.priority_advice(3, 2, "RB", 12, [], False, 1, gain_rank=40, teams=12)
    assert last.action == "claim" and last.worth_priority == 12
    tiny = wa.priority_advice(3, 2, "RB", 3, [], False, 1, gain_rank=9, teams=3)
    assert tiny.action == "claim" and tiny.worth_priority == 3


def _priority_plan(session, league_row, monkeypatch, my_priority, position=None, **rule_overrides):
    import json

    from services import league_moves

    rules = {**json.loads(league_row.scoring_settings), "waiver_type": "R", **rule_overrides}
    league_row.scoring_settings = json.dumps(rules)
    session.commit()
    insights = {"teams": [{"team": "My Squad", "is_users_team": True, "waiver_priority": my_priority},
                          {"team": "Rival", "waiver_priority": 1}], "winning_bids_by_week": []}
    monkeypatch.setattr(league_moves, "_insights", lambda *_args: insights)
    return league_moves.waiver_plan(session, league_row.id, position, limit=30)


WEAK = [("QB Player 20", "QB"), ("RB Player 50", "RB"), ("RB Player 49", "RB"), ("WR Player 60", "WR"),
        ("WR Player 59", "WR"), ("TE Player 25", "TE")]


def test_waiver_plan_gives_each_claim_the_priority_it_is_worth(session, league, monkeypatch):
    from tests.test_my_team import THEIRS, _seed_league

    _, ids = league
    row = _seed_league(session, ids, WEAK, THEIRS, name="Weak League")     # free agents help this roster
    # The seeded week has only a few useful free agents: drop the top-3 reserve and the
    # difference-maker shortcut so worth_priority is the plain gain rank.
    monkeypatch.setattr(wa, "TOP_PRIORITY", 0)
    monkeypatch.setattr(wa, "STARTER_WEEKS", 99)
    monkeypatch.setattr(wa, "BIG_GAIN", 999.0)
    plan = _priority_plan(session, row, monkeypatch, my_priority=2)
    assert plan["league_type"] == "priority"
    useful = sorted(((g, c, a) for g, c, _d, a in plan["rows"] if g >= wa.MIN_USEFUL_GAIN), key=lambda r: -r[0])
    assert len(useful) >= 3
    assert [a.worth_priority for _g, _c, a in useful] == list(range(1, len(useful) + 1))
    assert [a.action for _g, _c, a in useful] == ["claim", "claim"] + ["wait"] * (len(useful) - 2)
    worth = {c.player.player_id: a.worth_priority for _g, c, a in useful}

    # A position filter keeps the gain rank across every position (the best RB isn't #1).
    rbs = _priority_plan(session, row, monkeypatch, my_priority=2, position="RB")
    shown = [(c.player.player_id, a.worth_priority) for g, c, _d, a in rbs["rows"] if g >= wa.MIN_USEFUL_GAIN]
    assert shown and all(worth[pid] == n for pid, n in shown) and min(n for _p, n in shown) > 1

    # Two teams: N is capped at 2, and the last priority (#2) claims every useful player.
    last = _priority_plan(session, row, monkeypatch, my_priority=2, num_teams=2)
    advice = [a for g, _c, _d, a in last["rows"] if g >= wa.MIN_USEFUL_GAIN]
    assert advice and all(a.worth_priority <= 2 and a.action == "claim" for a in advice)


def _add_rookie(session) -> int:
    """A WR with this season's games only, so his projection leans on the no-prior-season prior."""
    pid = _add_player(session, "Rookie Wideout", "WR", "RKE")
    session.execute(insert(PlayerStatsWeekly), [stat_row(pid, CURRENT, w, "WR", 16.0, 5) for w in CURRENT_WEEKS[:3]])
    session.execute(insert(PlayerExpectedPointsWeekly),
                    [expected_row(pid, CURRENT, w, 16.0, 5) for w in CURRENT_WEEKS[:3]])
    session.commit()
    return pid


def test_league_size_and_superflex_change_values_never_points(session):
    rookie = _add_rookie(session)
    formats = [LeagueSettings(teams=8), LeagueSettings(teams=12), LeagueSettings(teams=14),
               LeagueSettings(teams=8, superflex=True)]
    built = [build_valuation(session, f) for f in formats]
    base = built[1]
    for other in built:
        assert {v.player_id: v.ros_ppg for v in other.values} == {v.player_id: v.ros_ppg for v in base.values}
        assert {v.player_id: v.base_ros_ppg for v in other.values} == {v.player_id: v.base_ros_ppg for v in base.values}
    assert rookie in base.by_id
    for other in (built[0], built[2], built[3]):
        assert other.replacement != base.replacement
        assert any(other.by_id[pid].trade_value != v.trade_value for pid, v in base.by_id.items())
    eight, superflex = built[0], built[3]
    qb = next(v for v in eight.values if v.position == "QB")
    assert superflex.by_id[qb.player_id].trade_value > qb.trade_value     # superflex: QBs are worth more


def test_replacement_eval_scores_every_scheme_on_seeded_data(session):
    from services import replacement_eval as re_

    league_ = LeagueSettings()
    pairs = [("RB", float(30 - i)) for i in range(40)]
    assert re_.scheme_levels("gwttkb", pairs, league_)["RB"] == pairs[round(2.83 * 12)][1]
    assert re_.scheme_levels("ours", pairs, league_)["RB"] == 0.0          # ours is deeper than a 40-RB pool
    assert re_.spearman([1, 2, 2, 3], [1, 2, 2, 3]) == pytest.approx(1.0)
    result = re_.run(session, [CURRENT], [3], league_, end_week=max(CURRENT_WEEKS))
    assert result["cuts"] == 1 and set(result["summary"]) == {"all", "mattered"}
    for metric in result["summary"].values():
        assert set(metric) == set(re_.SCHEMES)
        assert all(math.isnan(v) or -1.0 <= v <= 1.0 for row in metric.values() for v in row.values())
    assert re_.run(session, [1999], [3], league_, end_week=6)["cuts"] == 0           # no stats: skipped
