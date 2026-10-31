"""Agent waiver/trade tools on a real imported roster: league-aware evaluate_trade (lineup gain on both
sides, balance players only from the two rosters), structured waiver rows, and power-ranking market fields."""
import json

import pytest

from database.models import Roster, Team
from services import league_insights, league_trades
from tests.test_my_team import MINE, RULES, THEIRS, league  # noqa: F401  (fixture)
from tools.definitions import TOOLS
from tools.registry import ToolError

MINE_NAMES = {name for name, _ in MINE}
THEIR_NAMES = {name for name, _ in THEIRS}


def test_league_trade_scores_both_lineups(session, league):
    row, ids = league
    result = TOOLS.call(session, "evaluate_trade", {"give": ["WR Player 05"], "get": ["WR Player 02"]})
    assert result["league"] == "Family League" and result["partner"] == "Rival"
    assert isinstance(result["your_lineup_gain"], float) and isinstance(result["their_lineup_gain"], float)
    assert isinstance(result["acceptable_to_them"], bool)

    # Same scorer as the trade calculator, so both show the same gains.
    calculator = league_trades.trade_impact(session, row.id, [ids["WR Player 05"]], [ids["WR Player 02"]])
    assert result["your_lineup_gain"] == calculator["your_gain"]
    assert result["their_lineup_gain"] == calculator["their_gain"]


def test_league_balance_players_come_from_the_two_rosters_only(session, league):
    sides = set()
    for give, get in ((["QB Player 01"], ["RB Player 03"]), (["RB Player 01"], ["QB Player 02"]),
                      (["TE Player 01"], ["QB Player 02"])):
        result = TOOLS.call(session, "evaluate_trade", {"give": give, "get": get})
        for suggestion in result["balance_suggestions"]:
            sides.add(suggestion["add_to"])
            name = suggestion["player"]["name"]
            assert name in (THEIR_NAMES if suggestion["add_to"] == "get" else MINE_NAMES)   # ask them / add yours
            assert name not in give + get
    assert sides == {"get", "give"}                               # both directions exercised


def test_league_trade_errors_and_partial_cases(session, league):
    row, ids = league
    with pytest.raises(ToolError, match="already on your roster"):
        TOOLS.call(session, "evaluate_trade", {"give": ["WR Player 05"], "get": ["RB Player 01"]})
    third = Team(league_id=row.id, platform_team_id="third", name="Third", owner_display_name="T", is_users_team=False)
    session.add(third)
    session.flush()
    session.add(Roster(team_id=third.id, player_id=ids["QB Player 03"], week=7, roster_slot="QB"))
    session.commit()
    with pytest.raises(ToolError, match="different teams"):
        TOOLS.call(session, "evaluate_trade", {"give": ["WR Player 05"], "get": ["WR Player 02", "QB Player 03"]})
    not_mine = TOOLS.call(session, "evaluate_trade", {"give": ["WR Player 04"], "get": ["WR Player 02"]})
    assert "your_lineup_gain" not in not_mine and "isn't on your roster" in not_mine["lineup_note"]
    free_agent = TOOLS.call(session, "evaluate_trade", {"give": ["WR Player 05"], "get": ["WR Player 04"]})
    assert "your_lineup_gain" not in free_agent and "partner" not in free_agent
    assert "aren't on a team" in free_agent["lineup_note"]


def test_waiver_rows_are_structured_and_fit_the_budget(session, league):
    result = TOOLS.call(session, "get_league_waivers", {"limit": 10})
    assert result["league_type"] == "free_agency" and result["weeks_left"] >= 1
    assert len(json.dumps(result)) <= 6000
    for r in result["results"]:
        assert r["action"] in {"add", "wait"} and r["lineup_gain"] > 0
        assert r["drop"]["reason"]
        if "player" in r["drop"]:
            assert r["drop"]["player"].split(" (")[0] in MINE_NAMES and "ros_ppg" in r["drop"]


def test_faab_league_rows_carry_bid_market_and_worth(session, league):
    row, _ = league
    row.scoring_settings = json.dumps({**RULES, "uses_faab": True, "faab_budget": 100})
    session.commit()
    result = TOOLS.call(session, "get_league_waivers", {"limit": 10})        # no Yahoo: estimated market
    assert result["league_type"] == "faab" and result["faab_budget"] == 100
    assert result["results"] and len(json.dumps(result)) <= 6000
    for r in result["results"]:
        assert r["action"] in {"bid", "pass"} and r["estimate"] is True
        assert isinstance(r["market"], int) and isinstance(r["worth"], int)
        assert (r["action"] == "bid") == ("bid" in r)


def test_power_rankings_show_balances_priority_and_habits(session, league, monkeypatch):
    team = {"power_rank": 1, "team": "Rival", "is_users_team": False, "record": "5-2", "points_for": 800.0,
            "strength": 110.0, "playoff_odds": 0.8, "expected_wins": 9.1, "faab_balance": 42, "waiver_priority": 3,
            "tendencies": {"label": "Active trader", "trades": 3, "trades_for": ["RB"], "trades_away": ["WR"],
                           "picks_up": ["TE"]}}
    monkeypatch.setattr(league_insights, "build", lambda *a, **k: {"league": "Family League", "playoff_teams": 6,
                                                                   "teams": [team]})
    row = TOOLS.call(session, "get_power_rankings", {})["teams"][0]
    assert (row["faab_balance"], row["waiver_priority"]) == (42, 3)
    assert (row["trades_away"], row["picks_up"], row["trades_for"]) == (["WR"], ["TE"], ["RB"])
