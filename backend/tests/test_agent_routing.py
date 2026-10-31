"""Intent routing, chat memory, and the Yahoo-league agent tools (eval-case routing: test_agent_eval.py)."""
import json

import pytest

from services import agent_routing
from services.agent import clean_memory, memory_context, run_agent
from services.player_index import PlayerIndex
from tests.agent_fakes import ScriptedLLM
from tools.definitions import TOOLS

# Added by other parts of the same change; the lead registers them in tools/definitions.py.
NOT_YET_REGISTERED = {"start_sit", "get_injury_report"}
PLAYERS = ("Bijan Robinson", "Ja'Marr Chase", "Jahmyr Gibbs", "Puka Nacua", "Breece Hall")
# A realistic slice of the player table: nicknames, punctuation, suffixes, shared last names.
ROSTER = PLAYERS + (
    "Christian McCaffrey", "Luke McCaffrey", "Amon-Ra St. Brown", "Equanimeous St. Brown", "A.J. Brown",
    "Marquise Brown", "Jaxon Smith-Njigba", "Marvin Harrison Jr.", "Kyren Williams", "Mike Williams",
    "Saquon Barkley", "George Kittle", "CeeDee Lamb", "Justin Jefferson", "Van Jefferson", "Dalton Kincaid",
    "Tyrone Tracy Jr.", "Jalen Hurts", "Jordan Love", "Antonio Gibson", "Kenneth Walker III", "Travis Kelce",
)
INDEX = PlayerIndex(ROSTER, [n for n in ROSTER if n != "Antonio Gibson"])
TRADE, WAIVERS, WEEKLY, MATCHUP = "evaluate_trade", "get_league_waivers", "get_weekly_rankings", "get_my_matchup"
INJURY, NEWS, PROFILE, LOG = "get_injury_report", "get_player_news", "get_player_profile", "get_player_game_log"
COMPARE, ROS, SOS = "compare_players", "get_fantasy_rankings", "get_schedule_strength"

# (id, earlier user messages, question, tools that must all be routed). From the routing probe in the
# Ask AI council plan: last names, nicknames, typos, teams, follow-ups and words that over-matched.
ROUTING_CASES = (
    ("last-name-start", (), "Should I start Gibbs this week?", {"start_sit", WEEKLY}),
    ("nickname-cmc", (), "Is CMC playing Sunday?", {INJURY}),
    ("nickname-or", (), "ARSB or Puka in my flex?", {"start_sit", COMPARE}),
    ("first-name", (), "how is puka looking", {PROFILE}),
    ("nickname-ros", (), "JSN rest of season outlook", {PROFILE, ROS}),
    ("nickname-bust", (), "Is MHJ a bust?", {PROFILE}),
    ("first-names-vs", (), "bijan vs gibbs ros", {COMPARE}),
    ("typos", (), "Is Jahmyr Gibs worth starting over Breece Hal?", {COMPARE, "start_sit"}),
    ("apostrophe", (), "jamarr chase injury update", {INJURY, NEWS}),
    ("lowercase-full", (), "what's going on with puka nacua", {PROFILE, NEWS}),
    ("punctuation", (), "Amon Ra St Brown game log", {LOG}),
    ("team-position", (), "Lions WRs this week", {WEEKLY, INJURY}),
    ("team-defense", (), "Which defense should I stream against the Panthers?", {SOS, WAIVERS}),
    ("team-backfield", (), "Who gets the work in the Niners backfield now?", {INJURY}),
    ("handcuff", (), "Who is Bijan's handcuff?", {WAIVERS}),
    ("qb-streamer", (), "best QB streamer for week 9", {WAIVERS}),
    ("hurt-te", (), "I need a tight end, Kincaid is hurt", {WAIVERS, INJURY}),
    ("byes", (), "I have 3 guys on bye, what do I do?", {WAIVERS}),
    ("position-best", (), "Who are the best RBs right now?", {ROS}),
    ("faab", (), "how much should I bid on Tyrone Tracy", {WAIVERS}),
    ("cut", (), "who should I cut to make room?", {WAIVERS}),
    ("would-you-do", (), "would you do Saquon for Nacua and Kittle?", {TRADE}),
    ("give-get", (), "is this trade fair: I give Lamb, get Jefferson", {TRADE}),
    ("trade-target", (), "who on other teams could I realistically trade for at RB?", {"find_league_trades"}),
    ("odds", (), "what are my odds this week", {MATCHUP}),
    ("playoff-odds", (), "am I making the playoffs in the 12-team league?", {"get_playoff_outlook"}),
    ("sos", (), "which WRs have the easiest schedule the rest of the year", {SOS}),
    ("advanced-stat", (), "who leads the league in yards after contact", {"get_leaderboard"}),
    ("buy-low", (), "any buy-low candidates at WR?", {"get_buy_low_candidates"}),
    ("injury-news", (), "any injury news I should know about", {INJURY, NEWS}),
    ("weather", (), "will the weather affect the Bills game", {WEEKLY}),
    ("ceiling", (), "I'm a big underdog, who has the highest ceiling on my bench?", {MATCHUP, WEEKLY}),
    ("thursday", (), "Do I need to set my lineup before Thursday night?", {"start_sit"}),
    ("late-flex", (), "Should I put the Sunday night guy in my flex?", {WEEKLY}),
    ("snaps", (), "what's Kyren's snap share been", {LOG}),
    ("follow-up-3", ("Should I trade Lamb for Gibbs?", "What about adding Bijan instead?"),
     "and in the other league?", {TRADE, WAIVERS}),
    ("follow-up-pronoun", ("How is Jahmyr Gibbs doing?",), "should I start him?", {"start_sit", PROFILE}),
    ("follow-up-position", ("Any waiver pickups?",), "what about TEs", {WAIVERS}),
    ("check-out", (), "Check out my team and tell me what you think", {"get_my_team"}),
    ("recap", (), "Recap week 8 for me", {LOG}),
    ("rookies", (), "how have the rookie wide receivers done", {"get_leaderboard", ROS}),
    ("ppr-better", (), "Who is better in half PPR, a pass catching back or a WR3?", {ROS}),
    ("keeper", (), "Who should I keep next year?", {ROS}),
    ("stash-ir", (), "Can I stash anyone on IR?", {WAIVERS, INJURY}),
    ("over-under", (), "what's the over/under on the Lions game", {WEEKLY}),
    ("english-last-names", (), "Should I start Love or Hurts this week?", {"start_sit", COMPARE}),
    ("greeting", (), "hi", set()),
    ("thanks", (), "thanks, that helps", set()),
)
SMALLTALK_IDS = {"greeting", "thanks"}


def _plan(earlier, question):
    history = [{"role": "user", "content": t} for t in (*earlier, question)]
    return agent_routing.route_plan(history, INDEX)


@pytest.mark.parametrize("case", ROUTING_CASES, ids=lambda c: c[0])
def test_routing_case_gets_every_needed_tool(case):
    _, earlier, question, needed = case
    plan = _plan(earlier, question)
    assert needed <= set(plan.tools), (question, plan.intents, plan.tools)
    assert len(plan.tools) <= agent_routing.MAX_TOOLS


def test_routing_recall_and_menu_size_targets():
    plans = {c[0]: _plan(c[1], c[2]) for c in ROUTING_CASES}
    needed = sum(len(c[3]) for c in ROUTING_CASES)
    hits = sum(len(c[3] & set(plans[c[0]].tools)) for c in ROUTING_CASES)
    sizes = [len(p.tools) for p in plans.values()]
    assert hits / needed >= 0.95
    assert sum(sizes) / len(sizes) <= 8
    assert all(len(plans[i].tools) <= 3 and plans[i].intents == ("smalltalk",) for i in SMALLTALK_IDS)


def test_routing_sends_far_fewer_tools_than_all_and_always_the_basics():
    routed = agent_routing.route("Who should I pick up on waivers?")
    assert set(agent_routing.ALWAYS) <= set(routed)
    assert len(routed) < len(list(TOOLS)) / 2
    assert all(TOOLS.get(name) for name in routed if name not in NOT_YET_REGISTERED)


def test_unmatched_question_gets_the_general_set_and_follow_ups_keep_the_topic():
    assert set(agent_routing.DEFAULT) <= set(agent_routing.route("what do you think about my season"))
    history = [{"role": "user", "content": "Any trade ideas in my league?"}, {"role": "assistant", "content": "..."},
               {"role": "user", "content": "what about for a QB?"}]
    assert "find_league_trades" in agent_routing.route_plan(history).tools
    assert "find_league_trades" in agent_routing.route(agent_routing.recent_user_text(history))


def test_a_new_clear_topic_drops_the_old_one():
    plan = _plan(("Should I trade Lamb for Gibbs?",), "Who are the best waiver pickups at RB this week?")
    assert WAIVERS in plan.tools and TRADE not in plan.tools and plan.intents[0] == "waiver"


def test_words_that_used_to_match_everything_no_longer_do():
    assert "find_league_trades" not in agent_routing.route("any buy low candidates?")    # "buy" isn't a trade
    assert COMPARE not in agent_routing.route("Is my team good or not?")                 # "or" needs two players
    assert "explain_projection" not in agent_routing.route("Check out my standings")      # "out" alone
    assert "start_sit" not in agent_routing.route("Recap week 8 for me")                # "week" alone


def test_plan_reports_intents_and_resolved_players():
    plan = _plan((), "Should I start CMC or Gibbs?")
    assert plan.intents[0] == "start_sit" and "compare" in plan.intents
    assert [m.names for m in plan.mentions] == [("Christian McCaffrey",), ("Jahmyr Gibbs",)]
    assert set(plan.intents) <= set(agent_routing.INTENT_NAMES)
    assert agent_routing.route_plan([]).tools[:3] == list(agent_routing.ALWAYS)


def test_every_intent_names_real_tools():
    names = {t.name for t in TOOLS} | NOT_YET_REGISTERED
    for intent in agent_routing.INTENTS:
        assert set(intent.tools) <= names, intent.name
    assert set(agent_routing.DEFAULT) | set(agent_routing.PLAYER_TOOLS) | set(agent_routing.ALWAYS) <= names


def test_memory_is_trimmed_deduplicated_and_capped():
    facts = ["I'm 5-1", " I'm 5-1 ", "", *[f"fact {i}" for i in range(15)], "x" * 500]
    memory = clean_memory(facts)
    assert len(memory) == 10 and memory[-1] == "x" * 200 and "I'm 5-1" not in memory[-10:]
    assert memory_context([]) == "" and '"fact"' in memory_context(['fact'])


def test_remember_tool_returns_the_fact_and_the_prompt_carries_memory(session):
    client = ScriptedLLM([
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
