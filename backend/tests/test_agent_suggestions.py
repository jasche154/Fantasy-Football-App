from services.agent_suggestions import DEFAULTS, IGNORED, MAX_LENGTH, MAX_SUGGESTIONS, RULES, suggest
from tools.definitions import TOOLS


def test_follow_ups_come_from_the_most_recent_tool_first():
    result = suggest(["get_weekly_rankings", "get_my_matchup"], has_leagues=True)

    assert result[0] == RULES["get_my_matchup"][0][0]
    assert len(result) == MAX_SUGGESTIONS


def test_league_only_follow_ups_are_skipped_without_a_league():
    result = suggest(["get_league_waivers", "get_player_profile"], has_leagues=False)

    league_only = {text for rules in RULES.values() for text, needs_league in rules if needs_league}
    assert result and not set(result) & league_only


def test_no_useful_tool_falls_back_to_defaults():
    assert suggest([], has_leagues=True) == [text for text, _ in DEFAULTS][:MAX_SUGGESTIONS]
    assert suggest(["search_players", "remember"], has_leagues=False) == [
        text for text, needs_league in DEFAULTS if not needs_league][:MAX_SUGGESTIONS]


def test_suggestions_are_unique_and_short():
    every_tool = list(RULES)
    for has_leagues in (True, False):
        result = suggest(every_tool, has_leagues)
        assert len(result) == len(set(result)) <= MAX_SUGGESTIONS
    assert all(len(text) <= MAX_LENGTH for rules in (*RULES.values(), DEFAULTS) for text, _ in rules)


def test_unknown_tool_names_are_ignored():
    assert suggest(["not_a_tool"], has_leagues=False) == suggest([], has_leagues=False)


def test_every_registered_tool_has_rules_or_is_ignored():
    # A renamed or new tool must get follow-ups here (or be listed in IGNORED).
    registered = {tool.name for tool in TOOLS}
    assert registered - set(RULES) - IGNORED == set()
