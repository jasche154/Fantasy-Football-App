"""Follow-up questions shown as chips under an Ask AI answer.

They're fixed templates keyed on the tools the answer used, never text from the model, Yahoo
or news, so they can't carry injected instructions and cost no tokens. League-only follow-ups
are skipped when no Yahoo league is imported.
"""
from collections.abc import Sequence

MAX_SUGGESTIONS = 3
MAX_LENGTH = 80

# tool -> ((text, needs_league), ...). Order within a tool is priority order.
RULES: dict[str, tuple[tuple[str, bool], ...]] = {
    "start_sit": (("Am I favored in my matchup this week?", True),
                  ("Any injury news on my starters?", True)),
    "get_my_team": (("Who should I start at FLEX this week?", True),
                    ("Best waiver pickup for my team?", True)),
    "get_my_matchup": (("Should I play upside or safe players to win this week?", True),
                       ("Any injury news on my starters?", True)),
    "get_weekly_rankings": (("Who should I start at FLEX this week?", True),
                            ("Which of these players have the best matchups?", False)),
    "explain_projection": (("What's his floor and ceiling this week?", False),
                           ("Is he a buy or sell for the rest of the season?", False)),
    "get_league_waivers": (("How much FAAB should I bid on the top pickup?", True),
                           ("Who on my roster is safest to drop?", True)),
    "get_waiver_targets": (("Which of these pickups has the best schedule ahead?", False),),
    "get_trending_players": (("Are any trending players worth a waiver claim for me?", True),),
    "find_league_trades": (("Which of these trades helps my playoff odds most?", True),
                           ("Who in my league needs what I have?", True)),
    "find_trades": (("Is the top trade idea fair in rest-of-season value?", False),),
    "evaluate_trade": (("What would make this trade fair?", False),
                       ("How does this trade change my playoff odds?", True)),
    "get_league_rosters": (("Which teams are weakest at RB?", True),),
    "get_power_rankings": (("What are my playoff odds?", True),),
    "get_playoff_outlook": (("Who has the easiest playoff schedule on my roster?", True),
                            ("What trade would raise my playoff odds?", True)),
    "get_schedule_strength": (("Which defenses are easiest for RBs over the next 4 weeks?", False),),
    "get_injury_report": (("Who gains the most work if he sits?", False),
                          ("Who should replace him in my lineup?", True)),
    "get_player_news": (("Does this news change his projection?", False),),
    "get_player_profile": (("How has he scored over the last few weeks?", False),
                           ("Is he a buy-low or sell-high right now?", False)),
    "get_player_game_log": (("Is his usage trending up or down?", False),),
    "compare_players": (("Who should I start between them this week?", False),),
    "get_fantasy_rankings": (("Who are the best buy-low players right now?", False),),
    "get_expert_rankings": (("Where do experts disagree most with the app?", False),),
    "get_buy_low_candidates": (("Which buy-low player could I trade for in my league?", True),),
    "get_sell_high_candidates": (("Who on my roster should I sell high?", True),),
    "get_leaderboard": (("Who is scoring well above expected points?", False),),
}

# Tools that say nothing about what to ask next.
IGNORED = frozenset({"search_players", "remember", "list_leaderboard_metrics"})

DEFAULTS: tuple[tuple[str, bool], ...] = (
    ("Who should I start this week?", True),
    ("Best waiver pickup for my team?", True),
    ("Who are the best buy-low RBs right now?", False),
    ("Which WRs are scoring well above their expected points?", False),
)


def suggest(tool_names: Sequence[str], has_leagues: bool) -> list[str]:
    """Up to three follow-ups for an answer that used `tool_names` (most recent tool first)."""
    candidates = [text for name in reversed(tool_names) for text, needs_league in RULES.get(name, ())
                  if has_leagues or not needs_league]
    if not candidates:
        candidates = [text for text, needs_league in DEFAULTS if has_leagues or not needs_league]
    unique = list(dict.fromkeys(text for text in candidates if len(text) <= MAX_LENGTH))
    return unique[:MAX_SUGGESTIONS]
