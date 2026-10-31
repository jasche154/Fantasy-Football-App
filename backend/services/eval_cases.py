"""Saved Ask AI questions for the evaluation (services/agent_eval.py) and the offline routing test.

Each case names the tools the answer needs and what the answer must (not) say. About a third are
`holdout`: tune prompts, tools and routing on `dev`, and judge a change on `holdout` so the
evaluation doesn't reward fitting these exact questions. Multi-turn cases ask `before` first in
the same chat (the real assistant answers them) and score only the last question.

Checks are regexes over the answer, lowercased. Add a case (and its keywords in
services/agent_routing.py) whenever you add a tool.
"""
from dataclasses import dataclass

SPLITS = ("dev", "holdout")
# Players the cases name; the offline routing test resolves names against these.
PLAYERS = ("Bijan Robinson", "Ja'Marr Chase", "Jahmyr Gibbs", "Puka Nacua", "Breece Hall", "Lamar Jackson",
           "Christian McCaffrey", "Amon-Ra St. Brown", "Jaxon Smith-Njigba", "Saquon Barkley", "George Kittle",
           "CeeDee Lamb", "Justin Jefferson", "Kyren Williams", "Josh Allen", "Travis Kelce", "Malik Nabers",
           "De'Von Achane", "Derrick Henry", "Trey McBride")
# Questions about the user's leagues: the answer must never ask them to list their roster.
ROSTER_ASKS = ("which players", "list your", "who is on your", "who's on your", "share your roster",
               "tell me your roster", "provide your roster", "what players do you have")
DECISION = r"\b(start|sit|bench|flex|add|drop|cut|pick up|claim|bid|accept|decline|reject|propose|offer|hold|keep|" \
           r"sell|buy|trade for|stream|go with|play|target|avoid|counter|fade)\b"


@dataclass(frozen=True)
class Case:
    id: str
    question: str                          # the scored question (last user turn)
    expect_any: tuple[str, ...] = ()       # at least one of these tools must be called
    must_not_ask_roster: bool = False
    intent: str = ""
    before: tuple[str, ...] = ()           # earlier user turns in the same chat
    require_all: tuple[str, ...] = ()      # every one of these must be called
    forbid_tools: tuple[str, ...] = ()     # none of these may be called
    must_mention: tuple[str, ...] = ()     # each regex must match the answer
    forbid: tuple[str, ...] = ()           # no regex may match the answer
    decision: bool = False                 # the answer must make a call (start/add/accept ...)
    no_tools: bool = False                 # out of scope: answer without tools
    split: str = "dev"

    @property
    def turns(self) -> tuple[str, ...]:
        return (*self.before, self.question)


def _c(id_: str, intent: str, question: str, any_=(), **kw) -> Case:
    return Case(id_, question, tuple(any_), intent=intent, **kw)


MY = {"must_not_ask_roster": True}
H = {"split": "holdout"}
PCT = r"\d+(\.\d+)?\s*%"

CASES: tuple[Case, ...] = (
    # start / sit
    _c("my-team-start", "start_sit", "Who should I start this week?", ("get_my_team", "start_sit", "get_my_matchup"),
       decision=True, **MY),
    _c("my-team-flex", "start_sit", "Should I flex my WR3 or my RB3?", ("get_my_team", "start_sit",
       "get_weekly_rankings"), decision=True, **MY),
    _c("start-sit-pair", "start_sit", "Should I start Jahmyr Gibbs or Kyren Williams this week?", ("start_sit",),
       decision=True, must_mention=(r"gibbs|williams",)),
    _c("start-sit-nicknames", "start_sit", "CMC or Bijan in my flex?", ("start_sit", "compare_players",
       "get_weekly_rankings"), decision=True, **H),
    _c("start-sit-questionable", "start_sit", "Breece Hall is questionable, do I start him or play it safe?",
       ("start_sit", "get_injury_report"), decision=True, must_mention=(r"questionable|play|status|chance",)),
    _c("start-sit-upside", "start_sit", "I'm a big underdog this week, who on my bench has the highest ceiling?",
       ("get_my_matchup", "start_sit", "get_weekly_rankings"), decision=True, **MY, **H),
    _c("start-sit-te", "start_sit", "Trey McBride or George Kittle at TE this week?", ("start_sit",
       "compare_players"), decision=True, **H),
    # matchup
    _c("matchup-win", "matchup", "Am I going to win my matchup this week?", ("get_my_matchup",),
       must_mention=(PCT + "|chance|favored|underdog",), **MY),
    _c("matchup-opponent", "matchup", "Who's my opponent starting?", ("get_my_matchup",), **MY),
    _c("matchup-odds", "matchup", "What are my odds of beating my opponent this week?", ("get_my_matchup",),
       must_mention=(PCT,), **MY, **H),
    # trades
    _c("trade-league", "trade", "What trades can I make in my league for a running back?", ("find_league_trades",),
       decision=True, **MY),
    _c("trade-eval", "trade", "Should I trade Bijan Robinson for Ja'Marr Chase?", ("evaluate_trade",), decision=True,
       forbid=(r"\bguarantee",)),
    _c("trade-anyone", "trade", "Find me trade ideas", ("find_league_trades", "find_trades"), **MY),
    _c("trade-rosters", "trade", "Which team in my league needs a quarterback?", ("get_league_rosters",
       "find_league_trades")),
    _c("trade-would-you", "trade", "Would you do Saquon Barkley for Puka Nacua and George Kittle?",
       ("evaluate_trade",), decision=True, **H),
    _c("trade-give-get", "trade", "Is this fair: I give CeeDee Lamb, I get Justin Jefferson", ("evaluate_trade",),
       decision=True),
    _c("trade-target-rb", "trade", "Who on other teams could I realistically trade for at RB?",
       ("find_league_trades",), **MY, **H),
    # waivers
    _c("waiver-league", "waiver", "Who should I pick up on waivers?", ("get_league_waivers",), decision=True, **MY),
    _c("waiver-position", "waiver", "Best tight end to add this week?", ("get_league_waivers", "get_waiver_targets"),
       decision=True),
    _c("waiver-faab", "waiver", "How much FAAB should I bid on the top waiver RB?", ("get_league_waivers",
       "get_waiver_targets"), must_mention=(r"\$\s?\d+|\d+\s*(%|dollars|faab)|priority|no bid|free agent",)),
    _c("waiver-cut", "waiver", "Who should I cut to make room for a waiver pickup?", ("get_league_waivers",
       "get_my_team"), decision=True, **MY, **H),
    _c("waiver-stream-dst", "waiver", "Which defense should I stream this week?", ("get_league_waivers",
       "get_schedule_strength"), decision=True, **H),
    _c("waiver-handcuff", "waiver", "Who is Bijan Robinson's handcuff and is he worth stashing?",
       ("get_league_waivers", "get_injury_report", "get_player_profile")),
    # playoffs, schedule, league
    _c("playoffs-outlook", "playoffs", "How does my team look for the playoffs?", ("get_playoff_outlook",), **MY),
    _c("playoffs-sos", "schedule", "Which receivers have the easiest playoff schedule?", ("get_schedule_strength",)),
    _c("sos-week", "schedule", "Which defenses are easiest for RBs this week?", ("get_schedule_strength",)),
    _c("sos-ros", "schedule", "Which WRs have the easiest schedule the rest of the year?",
       ("get_schedule_strength",), **H),
    _c("playoff-fix", "playoffs", "Who should I pick up or trade for to fix my playoff lineup?",
       ("get_playoff_outlook",), decision=True, **MY),
    _c("playoff-odds", "league", "What are my odds to make the playoffs?", ("get_power_rankings",
       "get_playoff_outlook"), must_mention=(PCT + "|chance|odds",), **MY),
    _c("power-rankings", "league", "Who are the real contenders in my league?", ("get_power_rankings",)),
    _c("tendencies", "league", "Which managers in my league actually make trades?", ("get_power_rankings",), **H),
    _c("other-team", "league", "What does the best team in my league have on its roster?", ("get_league_rosters",
       "get_power_rankings")),
    # rankings, stats, regression, compare
    _c("rankings-ros", "rankings", "Top 10 rest of season wide receivers?", ("get_fantasy_rankings",)),
    _c("rankings-week", "rankings", "Top QBs this week?", ("get_weekly_rankings",)),
    _c("rankings-te-week", "rankings", "Rank the tight ends for this week", ("get_weekly_rankings",
       "get_fantasy_rankings"), **H),
    _c("rankings-experts", "rankings", "Where do the experts rank Malik Nabers?", ("get_expert_rankings",
       "get_fantasy_rankings", "get_player_profile")),
    _c("leaderboard", "stats", "Who leads the league in RYOE per attempt?", ("get_leaderboard",
       "list_leaderboard_metrics")),
    _c("leaderboard-yac", "stats", "Who leads the league in yards after contact?", ("get_leaderboard",), **H),
    _c("buy-low", "regression", "Give me some buy low candidates", ("get_buy_low_candidates",), decision=True),
    _c("sell-high", "regression", "Who's a sell high at receiver?", ("get_sell_high_candidates",), **H),
    _c("compare", "compare", "Compare Jahmyr Gibbs and Bijan Robinson", ("compare_players", "get_player_profile")),
    _c("compare-ros", "compare", "Ja'Marr Chase vs Justin Jefferson rest of season", ("compare_players",
       "get_fantasy_rankings"), **H),
    _c("explain", "rankings", "Why is Puka Nacua projected so high this week?", ("explain_projection",)),
    # injuries, news, game logs, trending
    _c("injury", "injury", "Is Breece Hall playing this week? He's questionable", ("get_injury_report",
       "explain_projection"), must_mention=(r"questionable|chance|status|play",)),
    _c("injury-who-benefits", "injury", "If Christian McCaffrey sits, who benefits?", ("get_injury_report",)),
    _c("injury-nickname", "injury", "Is CMC playing Sunday?", ("get_injury_report", "get_player_news"), **H),
    _c("injury-team", "injury", "Any injuries on the Lions I should worry about?", ("get_injury_report",), **H),
    _c("news", "news", "Any news on Lamar Jackson's ankle?", ("get_player_news", "get_injury_report")),
    _c("news-latest", "news", "What's the latest news on Travis Kelce?", ("get_player_news", "get_injury_report"),
       **H),
    _c("game-log", "game_log", "How has Jahmyr Gibbs done in his last 3 games?", ("get_player_game_log",
       "get_player_profile")),
    _c("game-log-snaps", "game_log", "What's Kyren Williams' snap share been lately?", ("get_player_game_log",), **H),
    _c("trending", "trending", "Who's trending on Sleeper?", ("get_trending_players",)),
    # memory
    _c("remember", "memory", "I'm 5-1 and my priority is the playoffs. Who should I target?", ("remember",)),
    # multi-turn: earlier questions are asked first in the same chat
    _c("mt-start-then-injury", "multi_turn", "what if Gibbs sits?", ("start_sit", "get_injury_report",
       "get_weekly_rankings"), before=("Who should I start this week?",), decision=True, **MY),
    _c("mt-trade-other-league", "multi_turn", "and in my other league?", ("find_league_trades", "evaluate_trade"),
       before=("Find me a trade for a running back in my league",), **MY),
    _c("mt-waiver-position", "multi_turn", "what about tight ends?", ("get_league_waivers",),
       before=("Who should I pick up on waivers?",), decision=True, **MY, **H),
    _c("mt-pronoun", "multi_turn", "should I start him this week?", ("start_sit", "get_weekly_rankings"),
       before=("How has Jahmyr Gibbs looked lately?",), decision=True),
    _c("mt-compare-follow", "multi_turn", "ok, and versus De'Von Achane?", ("compare_players", "start_sit"),
       before=("Compare Bijan Robinson and Derrick Henry",), **H),
    _c("mt-matchup-then-flip", "multi_turn", "any upside plays to flip it?", ("get_my_matchup", "start_sit",
       "get_weekly_rankings"), before=("Am I favored in my matchup this week?",), decision=True, **MY),
    # out of scope / smalltalk: no tools, short polite answer
    _c("oos-weather-city", "out_of_scope", "What's the weather in Paris today?", no_tools=True,
       forbid=(r"\d+\s*(°|degrees)",)),
    _c("oos-poem", "out_of_scope", "Write me a poem about my ex", no_tools=True, **H),
    _c("oos-thanks", "out_of_scope", "thanks, that helps", no_tools=True),
    _c("oos-other-sport", "out_of_scope", "Who will win the NBA finals?", no_tools=True, **H),
)
