"""The model's tools only read data. Text from Yahoo or news can steer what the model asks for, so
no tool may change the user's leagues or the database. `remember` only echoes a fact back; the
agent loop decides whether to keep it (user_said / grounding.safe_fact)."""
import re
from pathlib import Path

from tools.definitions import TOOLS

# Reviewed read-only tools. A new tool fails this test until someone checks it changes nothing and
# adds it here. Anything that writes (set lineup, add/drop, propose trade) needs the user to confirm
# it outside the LLM loop and must not be registered for the model.
READ_ONLY_TOOLS = frozenset({
    "search_players", "get_player_profile", "get_player_game_log", "compare_players", "get_fantasy_rankings",
    "list_leaderboard_metrics", "get_leaderboard", "evaluate_trade", "get_buy_low_candidates",
    "get_sell_high_candidates", "get_weekly_rankings", "get_schedule_strength", "explain_projection",
    "get_waiver_targets", "find_trades", "get_my_team", "get_league_waivers", "find_league_trades",
    "get_league_rosters", "get_my_matchup", "get_playoff_outlook", "get_power_rankings", "get_player_news",
    "get_expert_rankings", "get_trending_players", "start_sit", "get_injury_report",
})
ECHO_TOOLS = frozenset({"remember"})
TOOLS_DIR = Path(__file__).resolve().parent.parent / "tools"
DB_WRITE = re.compile(r"session\.(?:add|add_all|commit|delete|merge|flush)\(|(?<![.\w])(?:insert|update|delete)\(")


def test_every_registered_tool_is_reviewed_as_read_only():
    unreviewed = {tool.name for tool in TOOLS} - READ_ONLY_TOOLS - ECHO_TOOLS
    assert not unreviewed, f"review these tools for side effects, then add them to READ_ONLY_TOOLS: {unreviewed}"


def test_remember_only_echoes_the_fact(session):
    assert TOOLS.call(session, "remember", {"fact": "  I'm 5-3.  "}) == {"saved": "I'm 5-3."}
    assert not session.new and not session.dirty and not session.deleted


def test_the_tool_layer_never_writes_to_the_database():
    offenders = [f"{path.name}:{number}" for path in sorted(TOOLS_DIR.glob("*.py"))
                 for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                 if DB_WRITE.search(line)]
    assert not offenders, f"tools must only read data: {offenders}"
