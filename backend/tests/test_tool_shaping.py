"""Agent tool output: budget-safe JSON (tools/shaping.py), compact rows (tools/rows.py), league-aware
settings, and the tool fixes (K/DEF on my team, experts per position, clear errors)."""
import json
from types import SimpleNamespace

import pytest

from database.models import ExpertRanking, Player, Roster
from services import my_team as mt
from tests.test_injury_model import _seed_week
from tests.test_my_team import MINE, THEIRS, _seed_league
from tools import league_tools
from tools.definitions import TOOL_ERRORS, TOOLS
from tools.registry import Tool, ToolError
from tools.rows import header, matchup_words, opponent_str, player_str, schedule_words, sources_str, weekly_row
from tools.shaping import compact, fit

ROWS = [{"rank": i, "player": f"Player {i:03d} (WR, AAA)", "proj": 10.123456 + i, "note": None, "tags": []}
        for i in range(1, 301)]


# ---------- tools/shaping.py ----------

def test_compact_drops_empty_values_rounds_floats_and_keeps_the_input():
    value = {"a": None, "b": [], "c": {}, "d": 1.23456, "e": [{"x": None}, 2], "f": 0, "g": "", "h": False}
    assert compact(value) == {"d": 1.23, "e": [2], "f": 0, "g": "", "h": False}
    assert value["a"] is None and value["b"] == []                     # never mutated


def test_fit_returns_small_results_unchanged():
    assert json.loads(fit({"results": ROWS[:2]}, 6000)) == compact({"results": ROWS[:2]})


@pytest.mark.parametrize("budget", [400, 1500, 6000])
def test_fit_always_returns_valid_json_within_budget_keeping_top_rows(budget):
    text = fit({"week": 9, "results": ROWS}, budget, ("results",))
    data = json.loads(text)
    assert len(text) <= budget
    shown = data["truncated"]["shown"]
    assert data["truncated"] == {"shown": shown, "total": 300, "list": "results",
                                 "hint": "pass position/limit/league to narrow"}
    assert [r["rank"] for r in data["results"]] == list(range(1, shown + 1)) and data["week"] == 9


def test_fit_trims_trim_keys_first_then_the_largest_list():
    value = {"bench": ROWS[:40], "results": ROWS[:5], "other": ROWS[:60]}
    data = json.loads(fit(value, 2500, ("bench",)))
    assert data["truncated"]["list"] == "bench" and len(data["results"]) == 5
    nested = json.loads(fit({"me": {"starters": ROWS}}, 1000))
    assert nested["truncated"]["list"] == "me.starters"


def test_fit_wraps_top_level_lists_and_has_a_last_resort():
    wrapped = json.loads(fit(ROWS, 800))
    assert wrapped["items"] and wrapped["truncated"]["list"] == "items"
    huge = {"text": "x" * 5000}
    data = json.loads(fit(huge, 300))
    assert len(fit(huge, 300)) <= 300 and data["text"].endswith("…")
    assert json.loads(fit({"a": "x" * 50}, 20)) == {}


def test_tools_accept_trim_keys_and_default_to_none():
    assert Tool("t", "d", {"type": "object", "properties": {}}, lambda s: {}).trim_keys == ()
    assert TOOLS.get("get_weekly_rankings").trim_keys == ("results",)


# ---------- tools/rows.py ----------

def _weekly_player(**changes):
    matchup = SimpleNamespace(opponent="LAC", home=False, sos_factor=1.0249, implied_total=26.0, game_final=False)
    base = dict(player_id=1, name="Puka Nacua", position="WR", team="LA", matchup=matchup, projection=24.789,
                floor=9.1, ceiling=39.3, play_prob=1.0, status=None, pos_rank="WR1", actual=None,
                sources={"model": SimpleNamespace(points=22.8), "vegas_props": SimpleNamespace(points=21.84)})
    return SimpleNamespace(**{**base, **changes})


def test_weekly_row_is_compact_and_labeled():
    flag = SimpleNamespace(label="Snap share rising")
    row = weekly_row(_weekly_player(), flags=[flag], rank=3)
    assert row == {"rank": 3, "player": "Puka Nacua (WR, LA)", "pos_rank": "WR1", "proj": 24.8, "range": "9.1-39.3",
                   "play_prob": 1.0, "opp": "@ LAC", "matchup": "2% easier than avg vs WR", "implied_total": 26.0,
                   "flags": ["Snap share rising"], "sources": "props 21.8 | model 22.8"}
    assert len(json.dumps(row)) < 300


def test_row_words_never_flip_direction():
    assert matchup_words(0.94, "RB") == "6% harder than avg vs RB" and matchup_words(1.0) == "average matchup"
    assert schedule_words(1.052) == "5% easier than average (fantasy playoff weeks)"
    assert opponent_str(SimpleNamespace(opponent="SF", home=True, game_final=True)) == "vs SF (final)"
    assert player_str("Kicker Guy", "K") == "Kicker Guy (K)" and player_str("Nobody") == "Nobody"
    assert sources_str({}) is None
    assert header("half", 9, "Tintin") == {"league": "Tintin", "scoring": "half", "week": 9}


# ---------- league-aware settings ----------

@pytest.fixture
def league(session, seeded):
    _seed_week(session)
    ids = {p.full_name: p.id for p in session.query(Player).all()}
    return _seed_league(session, ids, MINE, THEIRS), ids


def test_settings_for_uses_the_named_or_only_league_and_explicit_args_win(session, league):
    row, _ = league
    row.scoring_settings = row.scoring_settings.replace('"ppr"', '"half"')
    session.commit()
    settings, found = league_tools.settings_for(session)
    assert found.id == row.id and settings.scoring == "half"
    assert league_tools.settings_for(session, "family", scoring="std")[0].scoring == "std"
    assert TOOLS.call(session, "get_weekly_rankings", {"limit": 2})["scoring"] == "half"


def test_settings_for_defaults_without_leagues_and_names_choices_when_unknown(session, league):
    _seed_league(session, league[1], MINE, THEIRS, name="Tintin")
    assert league_tools.settings_for(session)[1] is None             # two leagues, none named: app defaults
    with pytest.raises(ToolError, match="Try: league='Family League' or league='Tintin'"):
        TOOLS.call(session, "get_my_team", {})
    with pytest.raises(ToolError, match="No imported league matches"):
        TOOLS.call(session, "get_weekly_rankings", {"league": "nope"})


# ---------- tool fixes ----------

def test_my_team_lists_every_spot_with_names_alerts_and_locks(session, league):
    row, ids = league
    kicker = Player(full_name="Kicker Guy", position="K", nfl_team="AAA", gsis_id="00-kicker")
    session.add(kicker)
    session.flush()
    my_team_id = mt.find_league(session, row.id)[1].id
    session.add(Roster(team_id=my_team_id, player_id=kicker.id, week=7, roster_slot="K"))
    session.commit()
    result = TOOLS.call(session, "get_my_team", {})
    assert len(result["roster"]) == len(MINE) + 1
    assert any(r["player"].startswith("Kicker Guy") for r in result["roster"])
    names = [n for ids_ in result["best_lineup"].values() for n in ids_]
    assert names and not any(str(n).isdigit() for n in names)
    assert {"alerts", "locked", "note"} <= set(result) and "proj" in result["roster"][0]


def test_weekly_rankings_filters_players_and_reports_missing_ones(session, league):
    result = TOOLS.call(session, "get_weekly_rankings", {"players": ["RB Player 01", "Sell High Guy"]})
    assert [r["player"].split(" (")[0] for r in result["results"]] == ["RB Player 01"]
    assert result["not_projected"] == ["Sell High Guy"] and "get_injury_report" in result["note"]
    assert len(json.dumps(TOOLS.call(session, "get_weekly_rankings", {"limit": 50}))) < 6000


def test_explain_says_why_a_player_has_no_projection(session, league):
    with pytest.raises(ToolError, match="Sell High Guy is .* no projection. Try: get_injury_report"):
        TOOLS.call(session, "explain_projection", {"player": "Sell High Guy"})


def test_expert_rankings_cover_every_position_without_one(session, seeded):
    session.add_all([ExpertRanking(player_name=f"QB {i}", position="QB", source="nflverse_ffrankings",
                                   ranking_type="weekly", rank=i, ecr=float(i), pos_rank=f"QB{i}") for i in range(1, 30)])
    session.commit()
    result = TOOLS.call(session, "get_expert_rankings", {})
    positions = [r["player"].split("(")[1].split(",")[0].rstrip(")") for r in result["results"]]
    assert positions.count("QB") == 10 and "WR" in positions
    assert set(result["results"][0]) <= {"player", "pos_rank", "ecr"}


def test_value_errors_become_tool_errors_and_bare_value_error_is_not_trusted(session, seeded):
    assert ValueError not in TOOL_ERRORS
    with pytest.raises(ToolError, match="both sides"):
        TOOLS.call(session, "evaluate_trade", {"give": ["Buy Low Guy"], "get": ["Buy Low Guy"]})
    with pytest.raises(ToolError, match="Unknown metric"):
        TOOLS.call(session, "get_leaderboard", {"category": "rushing", "metric": "made_up"})


def test_leaderboard_metrics_fit_and_find_trades_takes_league(session, seeded):
    assert len(json.dumps(TOOLS.call(session, "list_leaderboard_metrics", {}))) < 3500
    assert "league" in TOOLS.get("find_trades").parameters["properties"]
    assert "my_league" not in TOOLS.get("find_trades").parameters["properties"]


def test_sibling_tools_name_each_other_and_no_fantasypros_wording():
    pairs = [("get_waiver_targets", "get_league_waivers"), ("find_trades", "find_league_trades"),
             ("get_weekly_rankings", "get_fantasy_rankings"), ("get_fantasy_rankings", "get_weekly_rankings"),
             ("get_expert_rankings", "get_fantasy_rankings"), ("get_league_waivers", "get_waiver_targets"),
             ("find_league_trades", "find_trades")]
    for tool, sibling in pairs:
        assert sibling in TOOLS.get(tool).description, tool
    assert not any("FantasyPros" in t.description for t in TOOLS)
