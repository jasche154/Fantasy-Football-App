"""Intent routing, chat memory, the eval set (offline), and the Yahoo-league agent tools."""
import json

import pytest

from services import agent_routing
from services.agent import clean_memory, memory_context, run_agent
from services.agent_eval import CASES, Case, score_live
from tools.definitions import TOOLS

PLAYERS = ("Bijan Robinson", "Ja'Marr Chase", "Jahmyr Gibbs", "Puka Nacua", "Breece Hall")


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_routing_sends_the_tools_each_eval_question_needs(case):
    routed = agent_routing.route(case.question, PLAYERS)
    assert set(case.expect_any) & set(routed), (case.question, routed)


def test_routing_sends_far_fewer_tools_than_all_and_always_the_basics():
    routed = agent_routing.route("Who should I pick up on waivers?")
    assert set(agent_routing.ALWAYS) <= set(routed)
    assert len(routed) < len(list(TOOLS)) / 2
    assert all(TOOLS.get(name) for name in routed)           # every routed name is a real tool


def test_unmatched_question_gets_the_general_set_and_follow_ups_keep_the_topic():
    assert set(agent_routing.DEFAULT) <= set(agent_routing.route("hello"))
    history = [{"role": "user", "content": "Any trade ideas in my league?"}, {"role": "assistant", "content": "..."},
               {"role": "user", "content": "what about for a QB?"}]
    assert "find_league_trades" in agent_routing.route(agent_routing.recent_user_text(history))


def test_every_group_names_real_tools():
    names = {t.name for t in TOOLS}
    for _, tools in agent_routing.GROUPS:
        assert set(tools) <= names
    assert set(agent_routing.DEFAULT) | set(agent_routing.PLAYER_TOOLS) | set(agent_routing.ALWAYS) <= names


def test_memory_is_trimmed_deduplicated_and_capped():
    facts = ["I'm 5-1", " I'm 5-1 ", "", *[f"fact {i}" for i in range(15)], "x" * 500]
    memory = clean_memory(facts)
    assert len(memory) == 10 and memory[-1] == "x" * 200 and "I'm 5-1" not in memory[-10:]
    assert memory_context([]) == "" and '"fact"' in memory_context(['fact'])


class ScriptedClient:
    def __init__(self, responses):
        self.responses, self.seen_tools = list(responses), []

    def complete(self, messages, tools):
        self.seen_tools.append([t["function"]["name"] for t in tools or []])
        self.messages = messages
        return self.responses.pop(0)


def test_remember_tool_returns_the_fact_and_the_prompt_carries_memory(session):
    client = ScriptedClient([
        {"content": None, "tool_calls": [{"id": "1", "name": "remember", "arguments": json.dumps({"fact": "I'm 5-1"})}]},
        {"content": "Noted.", "tool_calls": []},
    ])
    reply = run_agent(session, client, [{"role": "user", "content": "I'm 5-1, remember that"}], memory=["Playoffs first"])
    assert reply.memory == ("Playoffs first", "I'm 5-1")
    assert "Playoffs first" in client.messages[0]["content"]
    assert len(client.seen_tools[0]) < len(list(TOOLS))          # routed, not every tool


def test_league_tools_explain_when_no_league_is_imported(session):
    for name in ("get_league_rosters", "get_my_matchup", "get_playoff_outlook"):
        with pytest.raises(ValueError, match="No Yahoo league imported"):
            TOOLS.call(session, name, {})
    with pytest.raises(ValueError, match="No Yahoo league imported"):
        TOOLS.call(session, "find_trades", {})                    # no players and no league to take them from


def test_live_scoring_flags_roster_questions_and_wrong_tools():
    case = Case("x", "Who should I start?", ("get_my_team",), True)
    assert score_live(case, "Start Gibbs.", ["get_my_team"], [True]) == []
    problems = score_live(case, "Which players are on your team?", ["get_weekly_rankings"], [True])
    assert len(problems) == 2
