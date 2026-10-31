"""Lineup gain, waiver advice by league type, trade scoring, streaming, and the injury-aware season sim."""
from types import SimpleNamespace

import numpy as np
import pytest

from services import league_insights as li
from services import lineup_gain as lg
from services import waiver_advice as wa
from services.league import LeagueSettings
from tests.test_my_team import league  # noqa: F401  (fixture)

SETTINGS = LeagueSettings(starters={"QB": 1, "RB": 1, "WR": 1, "TE": 0, "FLEX": 0})


def _v(pid, position, ppg, team="T", availability=1.0):
    return SimpleNamespace(player_id=pid, position=position, ros_ppg=ppg, team=team, availability=availability)


class FlatProjector(lg.Projector):
    """Healthy rate every week, no matchups or byes (for arithmetic you can check by hand)."""

    def __init__(self, statuses=None):
        super().__init__(None, statuses or {}, {}, 9)

    def _points(self, value, week):
        ahead = week - self.current_week
        return self.healthy_rate(value) * (self.available(value, ahead) if ahead > 0 else 1.0)


def test_horizon_weights_regular_weeks_by_distance_and_playoffs_by_odds():
    h = lg.horizon(14, 17, [15, 16, 17], 0.8)
    assert h.weights == {14: 1.0, 15: 0.8, 16: 0.8, 17: 0.8}
    assert lg.horizon(10, 12, [16], 0.0).weights[12] == pytest.approx(0.95 ** 2)
    assert lg.horizon(15, 16, [15, 16], 0.0).weights[15] == lg.PLAYOFF_MIN


def test_injured_players_come_back_on_the_measured_timelines():
    proj = FlatProjector({1: SimpleNamespace(status="Out"), 2: SimpleNamespace(status="IR"), 3: SimpleNamespace(status=None)})
    out, ir, healthy = _v(1, "RB", 10), _v(2, "RB", 10), _v(3, "QB", 20)
    rb, qb = lg.healthy_share("RB"), lg.healthy_share("QB")
    assert rb == pytest.approx(1 - 0.043 / (0.4 + 0.043)) and qb == pytest.approx(1 - 0.088 / (0.4 + 0.088))
    assert proj.available(out, 1) == pytest.approx((1 - 0.6) * rb)
    assert proj.available(out, 3) == pytest.approx((1 - 0.6 ** 3) * rb)
    assert proj.available(ir, 3) == 0.0 and proj.available(ir, 4) == pytest.approx(rb)
    assert proj.available(healthy, 2) == pytest.approx(qb)
    assert proj.healthy_rate(_v(4, "WR", 6.0, availability=0.5)) == 12.0      # a hurt player's healthy rate


def test_gain_and_the_named_drop_follow_the_best_lineup():
    roster = [_v(1, "QB", 20), _v(2, "RB", 12), _v(3, "WR", 10), _v(4, "WR", 4)]
    h, proj = lg.horizon(9, 10, [], 1.0), FlatProjector()
    upgrade = _v(9, "WR", 15)
    assert lg.gain(roster, h, proj, SETTINGS, add=[upgrade], drop=[roster[3]]) == pytest.approx(
        5 * (1 + 0.95 * lg.healthy_share("WR")), 0.01)
    gain, drop = lg.best_add(roster, roster, upgrade, h, proj, SETTINGS)
    assert drop.player_id == 4 and gain > 0                                      # the bench WR goes
    no_room = lg.best_add(roster, [], _v(10, "RB", 1), h, proj, SETTINGS)
    assert no_room == (0.0, None)


def test_handcuffs_to_your_own_starter_are_protected():
    roster = [_v(1, "QB", 20), _v(2, "RB", 15, team="DET"), _v(5, "RB", 3, team="DET"), _v(3, "WR", 10), _v(4, "WR", 2)]
    h, proj = lg.horizon(9, 9, [], 1.0), FlatProjector()
    assert lg.handcuffs(roster, h, proj, SETTINGS) == {5}
    _, drop = lg.best_add(roster, roster, _v(9, "WR", 4), h, proj, SETTINGS, protected={5})
    assert drop.player_id == 4


def test_faab_bids_use_the_market_cap_by_worth_and_fall_back_to_estimates():
    rivals = [{"balance": 60, "needs": ("RB",)}, {"balance": 20, "needs": ("WR",)}, {"balance": 90, "needs": ()}]
    no_history = wa.faab_advice(gain=20, rank=1, position="RB", balance=80, budget=100, weeks_left=8,
                                rivals=rivals, winning_bids_by_week=[], week=9)
    assert no_history.action == "bid" and no_history.estimate and no_history.bid == 16        # 15% x demand 1.075
    assert no_history.bid <= 61                                                             # richest interested + 1
    history = [[30, 12, 5], [26, 10, 4], [40, 8], [22, 9]]
    market = wa.faab_advice(20, 1, "RB", 80, 100, 8, rivals, history, 9)
    assert not market.estimate and market.market > 15
    weak = wa.faab_advice(gain=1.5, rank=1, position="RB", balance=80, budget=100, weeks_left=8,
                          rivals=rivals, winning_bids_by_week=history, week=9)
    assert weak.action == "pass" and "worth about" in weak.note


def test_priority_and_free_agency_advice():
    spend = wa.priority_advice(gain=9, rank=1, position="RB", my_priority=2, teams_ahead_needing=["A"],
                               is_handcuff_to_injured=False, starts_weeks=1)
    assert spend.action == "claim" and "A (ahead of you)" in spend.note
    save = wa.priority_advice(3, 2, "RB", 2, [], False, 1)
    assert save.action == "wait" and "#2 priority" in save.note
    assert wa.priority_advice(3, 2, "RB", 8, [], False, 1).action == "claim"            # low priority: free to use
    assert wa.priority_advice(3, 2, "RB", 1, [], False, 3).action == "claim"            # a 3-week starter
    assert wa.priority_advice(0.4, 1, "RB", 9, [], False, 0).action == "wait"           # marginal
    assert wa.free_agent_advice(4.0).action == "add" and wa.free_agent_advice(0.5).action == "wait"
    assert wa.league_type({"uses_faab": True}) == "faab"
    assert wa.league_type({"waiver_type": "R"}) == "priority" and wa.league_type({}) == "free_agency"


def test_waiver_plan_and_trades_on_a_real_roster(client, session, league):
    row, _ = league
    body = client.get(f"/api/my-team/{row.id}/waivers").json()
    assert body["league_type"] == "free_agency"                                          # test rules: no waivers set
    assert body["results"] and all("advice" in r and "lineup_gain" in r for r in body["results"])
    gains = [r["lineup_gain"] for r in body["results"]]
    assert gains == sorted(gains, reverse=True)
    trades = client.get(f"/api/my-team/{row.id}/trades").json()
    for team in trades["teams"]:
        for offer in team["offers"]:
            assert offer["your_gain"] > 0
    grid = client.get(f"/api/my-team/{row.id}/streaming").json()
    assert "DST" in grid["grids"] and len(grid["weeks"]) <= 3


def test_streaming_path_only_switches_for_a_real_edge():
    from services.streaming import suggested_path

    rows = [{"name": "Mine", "by_week": {"1": {"projection": 7}, "2": {"projection": 5}, "3": {"projection": 6}}},
            {"name": "FA", "by_week": {"1": {"projection": 8}, "2": {"projection": 9}, "3": {"projection": 6.5}}}]
    assert suggested_path(rows, [1, 2, 3], {"Mine"}) == ["Hold Mine for week 1", "Add FA for weeks 2-3"]


def test_season_sim_spreads_odds_with_team_uncertainty_and_injuries():
    teams, weeks = ["a", "b", "c", "d"], range(8, 15)
    strength = {t: {w: s for w in weeks} for t, s in zip(teams, (110, 100, 100, 90))}
    games = [g for w in weeks for g in ((w, "a", "b"), (w, "c", "d"))]
    sharp = li.simulate_season(teams, strength, {t: 3 for t in teams}, {t: 0 for t in teams}, games, 2, 4000, season_sd=0)
    wide = li.simulate_season(teams, strength, {t: 3 for t in teams}, {t: 0 for t in teams}, games, 2, 4000)
    assert wide["a"]["playoff_odds"] < sharp["a"]["playoff_odds"] and wide["d"]["playoff_odds"] > sharp["d"]["playoff_odds"]
    lineups = {8: [(1, "QB", 20.0, 12.0)], 9: [(1, "QB", 20.0, 12.0)]}
    losses = li.injury_losses(lineups, {1: 1}, np.random.default_rng(0), 5000)
    assert losses[8].mean() == pytest.approx(8.0)                                      # out now: loses 20 - 12
    assert 3.0 < losses[9].mean() < 6.0                                                # stays out 60%, else hazard


def test_playoff_rate_uses_the_healthy_rate():
    from services.playoffs import playoff_rate

    assert playoff_rate(_v(1, "RB", 6.0, availability=0.5)) == pytest.approx(12.0 * lg.healthy_share("RB"))


def test_props_budget_guard_counts_only_your_own_rosters(session, league, monkeypatch):
    from database.models import Player
    from services import system_status, weekly_refresh

    row, ids = league
    system_status.record(session, "odds_credits", {"remaining": 0})
    monkeypatch.setattr(weekly_refresh.odds_api, "credits_needed", lambda session, season: 1000)
    teams = weekly_refresh._props_budget_teams(session, 2026)
    mine = {session.get(Player, ids[name]).nfl_team for name in ("QB Player 01", "RB Player 20", "WR Player 01")}
    rival = session.get(Player, ids["QB Player 02"]).nfl_team
    assert mine <= teams and (rival in mine or rival not in teams or rival in teams & mine)
    system_status.record(session, "odds_credits", {"remaining": 5000})
    assert weekly_refresh._props_budget_teams(session, 2026) is None              # enough credits: buy everything
