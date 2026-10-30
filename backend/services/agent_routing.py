"""Intent routing: send the model only the tools a question is likely to need.

Every tool schema costs tokens on every round, and Groq's free tier allows about 8,000 tokens
a minute. A smaller menu also means fewer wrong tool picks. Routing is keyword-based on the
user's last two messages (so "what about RBs?" keeps the previous topic). Nothing matched:
a general set. The model can't call a tool it wasn't sent, so the groups err on the side of
including a tool.
"""
import re
from collections.abc import Iterable, Mapping, Sequence

ALWAYS = ("search_players", "get_my_team", "remember")

# (keywords, tools). Keywords match whole words or phrases, case-insensitively.
GROUPS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("start", "sit", "bench", "lineup", "flex", "this week", "play", "week"),
     ("get_weekly_rankings", "explain_projection", "get_my_matchup")),
    (("matchup", "opponent", "win", "winning", "beat", "against"),
     ("get_my_matchup", "get_weekly_rankings")),
    (("trade", "trades", "deal", "offer", "swap", "sell", "buy", "acquire", "get for", "give up"),
     ("find_league_trades", "find_trades", "evaluate_trade", "get_league_rosters")),
    (("waiver", "waivers", "pickup", "pick up", "add", "drop", "faab", "free agent", "free agents", "stream"),
     ("get_league_waivers", "get_waiver_targets", "get_trending_players")),
    (("playoff", "playoffs", "championship", "weeks 15", "weeks 16", "week 16", "week 17", "week 18"),
     ("get_playoff_outlook", "get_schedule_strength", "find_league_trades")),
    (("schedule", "sos", "strength of schedule", "defense", "defenses", "matchups"),
     ("get_schedule_strength",)),
    (("rank", "ranks", "ranking", "rankings", "top", "best", "ecr", "tier", "rest of season", "ros"),
     ("get_fantasy_rankings", "get_expert_rankings", "get_weekly_rankings")),
    (("stat", "stats", "leader", "leaders", "leaderboard", "ryoe", "epa", "separation", "yac", "cpoe", "target share",
      "air yards", "advanced", "efficiency", "efficient"),
     ("list_leaderboard_metrics", "get_leaderboard")),
    (("buy low", "buy lows", "buy-low", "sell high", "sell highs", "sell-high", "regress", "regression", "bounce back", "breakout", "unlucky",
      "lucky", "due"),
     ("get_buy_low_candidates", "get_sell_high_candidates")),
    (("compare", "vs", "versus", "or", "better", "who should"),
     ("compare_players", "get_player_profile", "get_weekly_rankings")),
    (("projection", "projected", "why", "explain", "injury", "injured", "questionable", "out"),
     ("explain_projection", "get_weekly_rankings")),
    (("game log", "last week", "last game", "recent", "trend", "games"),
     ("get_player_game_log", "get_player_profile")),
    (("trending", "hot", "most added", "most dropped"),
     ("get_trending_players",)),
    (("roster", "rosters", "team", "teams", "league", "manager", "managers", "everyone"),
     ("get_league_rosters", "get_my_team")),
)
PLAYER_TOOLS = ("get_player_profile", "get_player_game_log", "explain_projection", "compare_players")
DEFAULT = ("get_player_profile", "get_weekly_rankings", "get_fantasy_rankings", "get_league_waivers",
           "find_league_trades", "get_my_matchup")
ROUTING_MESSAGES = 2


def _has(text: str, phrase: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", text) is not None


def recent_user_text(history: Sequence[Mapping[str, str]], count: int = ROUTING_MESSAGES) -> str:
    return " ".join([m["content"] for m in history if m.get("role") == "user"][-count:])


def route(text: str, player_names: Iterable[str] = ()) -> list[str]:
    """Tool names for this question, in a stable order (ALWAYS first)."""
    lowered = text.lower()
    chosen: list[str] = list(ALWAYS)
    matched = False
    for keywords, tools in GROUPS:
        if any(_has(lowered, k) for k in keywords):
            matched = True
            chosen += [t for t in tools if t not in chosen]
    if any(name.lower() in lowered for name in player_names):
        matched = True
        chosen += [t for t in PLAYER_TOOLS if t not in chosen]
    if not matched:
        chosen += [t for t in DEFAULT if t not in chosen]
    return chosen
