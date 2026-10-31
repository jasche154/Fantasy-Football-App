"""Intent routing: send the model only the tools a question is likely to need.

Every tool schema costs tokens on every round, and Groq's free tier allows about 8,000 tokens
a minute. A smaller menu also means fewer wrong tool picks. Each intent has strong keywords
(3 points), weak ones (1 point) and regex patterns (3 points). The best few intents that score
DEFAULT_THRESHOLD or more get their tools, players named in the question (full names, last
names, nicknames, typos via services.player_index) add the player tools, and the total is capped
at MAX_TOOLS. Follow-ups ("what about RBs?", "start him?") borrow the topic and players of the
previous messages; a new question with its own clear topic does not. Greetings and thanks get
only the basics. Nothing matched: a general set.
"""
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from services.player_index import Mention, PlayerIndex

ALWAYS = ("search_players", "get_my_team", "remember")
MAX_TOOLS = 10
MAX_INTENTS = 3
DEFAULT_THRESHOLD = 2.0
STRONG, WEAK, PATTERN = 3.0, 1.0, 3.0
TEAM_BOOST = 2.0                       # a team name ("Lions") points at that team's injury report
FOLLOW_UP_WEIGHTS = (0.8, 0.7, 0.6)    # previous user messages, newest first
ROUTING_MESSAGES = 1 + len(FOLLOW_UP_WEIGHTS)
SMALLTALK_MAX_WORDS = 5
TIE_BREAK = 1e-4                       # per character: never outweighs a keyword


@dataclass(frozen=True)
class Intent:
    name: str
    tools: tuple[str, ...]                 # priority order: the cap drops from the end
    strong: tuple[str, ...] = ()           # whole words/phrases; a trailing * matches any ending
    weak: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()         # regexes over the lowercased message


INTENTS: tuple[Intent, ...] = (
    Intent("start_sit", ("start_sit", "get_weekly_rankings", "get_my_matchup", "explain_projection"),
           strong=("start", "starting", "sit", "sitting", "bench", "benched", "lineup", "flex", "late flex",
                   "upside play", "safe play", "floor", "ceiling", "weather", "wind", "snow", "rain", "vegas",
                   "spread", "over/under", "implied total", "shootout", "kickoff", "inactives", "locked"),
           weak=("this week", "week", "play", "playing", "upside", "safe", "boom", "sunday", "monday night",
                 "thursday", "tnf", "snf", "mnf", "total"),
           patterns=(r"\b(start|play|flex)\b.+\b(or|over|vs)\b",)),
    Intent("matchup", ("get_my_matchup", "get_weekly_rankings"),
           strong=("matchup", "opponent", "win", "winning", "beat", "favored", "underdog", "win probability"),
           weak=("against", "odds", "chances")),
    Intent("trade", ("evaluate_trade", "find_league_trades", "get_league_rosters", "find_trades"),
           strong=("trade", "trades", "trading", "traded", "deal", "offer", "offered", "counter", "counteroffer",
                   "acquire", "give up", "get for", "swap", "what would it take"),
           weak=("sell", "fair", "accept", "package", "worth it"),
           patterns=(r"\bwould you (do|take|accept|make)\b", r"\bi give\b.+\bget\b")),
    Intent("waiver", ("get_league_waivers", "get_waiver_targets", "get_trending_players"),
           strong=("waiver", "waivers", "waiver wire", "pickup", "pickups", "pick up", "add", "drop", "cut",
                   "release", "faab", "bid*", "free agent", "free agents", "stream*", "handcuff*", "stash",
                   "replacement", "fill in", "bye", "byes", "claim", "adding", "added", "need a", "looking for a"),
           weak=("replace", "make room", "who to get")),
    Intent("playoffs", ("get_playoff_outlook", "get_schedule_strength", "get_power_rankings"),
           strong=("playoff*", "championship", "make the playoffs", "weeks 15", "week 15", "week 16", "week 17"),
           weak=("postseason",),
           patterns=(r"\bplayoff (lineup|roster|run|push|weeks|schedule)\b", r"\bfor the playoffs\b")),
    Intent("schedule", ("get_schedule_strength",),
           strong=("schedule", "sos", "strength of schedule", "defense", "defenses", "easiest", "toughest",
                   "matchups"),
           weak=("easy", "tough", "soft")),
    Intent("rankings", ("get_fantasy_rankings", "get_weekly_rankings", "get_expert_rankings"),
           strong=("rank", "ranks", "ranking", "rankings", "ros", "rest of season", "rest of the year", "ecr",
                   "tier*", "keeper", "keepers", "keep", "dynasty", "next year", "rookie*"),
           weak=("top", "best", "better", "value", "worth", "ppr", "outlook")),
    Intent("stats", ("get_leaderboard", "list_leaderboard_metrics"),
           strong=("stat", "stats", "leader*", "leads", "lead the league", "ryoe", "epa", "separation", "yac",
                   "cpoe", "air yards", "advanced", "efficien*", "yards after contact", "yards after catch", "rookie*"),
           weak=("most yards", "target share")),
    Intent("regression", ("get_buy_low_candidates", "get_sell_high_candidates"),
           strong=("buy low", "buy lows", "buy-low", "sell high", "sell highs", "sell-high", "regress*",
                   "bounce back", "breakout", "unlucky", "lucky", "due for", "bust"),
           weak=("due",)),
    Intent("compare", ("compare_players", "get_player_profile"),
           strong=("compare", "comparison", "versus"),
           weak=("better", "vs")),
    Intent("injury", ("get_injury_report", "get_player_news", "explain_projection"),
           strong=("injury", "injuries", "injured", "hurt", "questionable", "doubtful", "ir", "injured reserve",
                   "inactive", "ruled out", "practice", "practicing", "limited", "dnp", "concussion",
                   "hamstring", "ankle", "knee", "who benefits", "backfield", "out for", "is out",
                   "game-time", "returning", "return", "healthy", "suspended"),
           weak=("status", "health", "out"),
           patterns=(r"\b(is|will|does)\s+\w+(\s+\w+)?\s+(play|playing|suit up|be active|go)\b",)),
    Intent("news", ("get_player_news", "get_injury_report"),
           strong=("news", "update", "updates", "latest on", "what happened", "going on with", "heard",
                   "report", "reports"),
           weak=("rumor*",)),
    Intent("game_log", ("get_player_game_log", "get_player_profile"),
           strong=("game log", "last week", "last game", "last 3", "last three", "recent*", "trend", "recap",
                   "snap*", "lately", "been doing"),
           weak=("games", "doing", "looking", "week 1", "week 2", "week 3", "week 4", "week 5", "week 6",
                 "week 7", "week 8")),
    Intent("trending", ("get_trending_players",),
           strong=("trending", "hot", "most added", "most dropped", "buzz")),
    Intent("league", ("get_power_rankings", "get_league_rosters"),
           strong=("power", "standings", "standing", "contender*", "tendenc*", "trader", "traders", "manager",
                   "managers", "best team", "worst team", "leaguemate*", "league mates"),
           weak=("league", "everyone", "improve")),
    Intent("roster", ("get_league_rosters",),
           strong=("roster", "rosters", "rostered", "who has", "who owns", "needs a"),
           weak=("team", "teams", "my team", "depth")),
)
INTENT_NAMES = tuple(i.name for i in INTENTS) + ("smalltalk",)
PLAYER_TOOLS = ("get_player_profile", "get_injury_report", "explain_projection", "get_player_game_log",
                "get_player_news", "compare_players")
DEFAULT = ("get_weekly_rankings", "get_fantasy_rankings", "get_player_profile", "get_league_waivers",
           "get_my_matchup")
COMPARE_CUE = re.compile(r"\b(or|vs|versus|over|better|than|compare|instead of)\b")
FOLLOW_UP = re.compile(r"^\s*(and|what about|how about|also|same|but|ok so|then)\b|\b(him|he|his|them|they|"
                       r"that guy|those guys|instead|other league|other one|either|both|same for)\b")
SMALLTALK = re.compile(r"^\s*(hi|hey|hello|yo|sup|thanks|thank you|thx|ty|ok|okay|cool|great|nice|got it|"
                       r"sounds good|perfect|awesome|appreciate it|good morning|lol)\b")
TEAMS = frozenset("""
cardinals falcons ravens bills panthers bears bengals browns cowboys broncos lions packers texans colts jaguars
jags chiefs raiders chargers rams dolphins vikings patriots pats saints giants jets eagles steelers niners 49ers
seahawks buccaneers bucs titans commanders
""".split())


@dataclass(frozen=True)
class RoutePlan:
    tools: list[str]
    intents: tuple[str, ...]
    mentions: tuple[Mention, ...]


def _phrase_regex(phrase: str) -> re.Pattern:
    stem = phrase.endswith("*")
    body = re.escape(phrase.rstrip("*")) + (r"[a-z0-9]*" if stem else "")
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


_COMPILED = {i.name: ([_phrase_regex(p) for p in i.strong], [_phrase_regex(p) for p in i.weak],
                      [re.compile(p) for p in i.patterns]) for i in INTENTS}


def _has(text: str, phrase: str) -> bool:
    return _phrase_regex(phrase).search(text) is not None


def score_intents(text: str, mentions: Sequence[Mention] = ()) -> dict[str, float]:
    """Points per intent for one message (only intents that scored)."""
    lowered = text.lower()
    scores: dict[str, float] = {}
    for intent in INTENTS:
        strong, weak, patterns = _COMPILED[intent.name]
        hits = [m for r in (*strong, *weak, *patterns) if (m := r.search(lowered))]
        points = (STRONG * min(2, sum(bool(r.search(lowered)) for r in strong))
                  + WEAK * sum(bool(r.search(lowered)) for r in weak)
                  + PATTERN * any(r.search(lowered) for r in patterns))
        if points:      # ties go to the topic mentioned first
            scores[intent.name] = points - TIE_BREAK * min(m.start() for m in hits)
    if len(mentions) >= 2 and COMPARE_CUE.search(lowered):     # "Gibbs or Bijan?", "X over Y"
        scores["compare"] = scores.get("compare", 0) + STRONG
    if any(word in TEAMS for word in re.findall(r"[a-z0-9]+", lowered)):
        scores["injury"] = scores.get("injury", 0) + TEAM_BOOST
    return scores


def recent_user_text(history: Sequence[Mapping[str, str]], count: int = ROUTING_MESSAGES) -> str:
    return " ".join([m["content"] for m in history if m.get("role") == "user"][-count:])


def _passes(points: float) -> bool:
    return points >= DEFAULT_THRESHOLD - 0.05        # the tie-break nudge never drops an intent


def _is_smalltalk(text: str) -> bool:
    return len(text.split()) <= SMALLTALK_MAX_WORDS and SMALLTALK.search(text.lower()) is not None


def route_plan(history: Sequence[Mapping[str, str]] | str, index: PlayerIndex | None = None,
               max_tools: int = MAX_TOOLS) -> RoutePlan:
    """Tools, intents and named players for the latest user message (ALWAYS tools first)."""
    if isinstance(history, str):
        history = [{"role": "user", "content": history}]
    texts = [m.get("content") or "" for m in history if m.get("role") == "user"][-ROUTING_MESSAGES:]
    if not texts:
        return RoutePlan(list(ALWAYS) + list(DEFAULT), (), ())
    current, earlier = texts[-1], texts[:-1][::-1]                # earlier: newest first
    found = [index.mentions(t) if index else () for t in texts]
    mentions, earlier_mentions = found[-1], found[:-1][::-1]
    scores = score_intents(current, mentions)
    if not scores and not mentions and _is_smalltalk(current):
        return RoutePlan(list(ALWAYS), ("smalltalk",), ())
    follow_up = FOLLOW_UP.search(current.lower()) is not None or len(current.split()) <= 4
    if follow_up or not _passes(max(scores.values(), default=0)):
        for weight, text, older in zip(FOLLOW_UP_WEIGHTS, earlier, earlier_mentions):
            for name, points in score_intents(text, older).items():
                scores[name] = scores.get(name, 0) + weight * points
        if not mentions:
            mentions = next((m for m in earlier_mentions if m), ())
    ranked = sorted((s, -i, INTENTS[i].name) for i, s in
                    ((i, scores.get(intent.name, 0)) for i, intent in enumerate(INTENTS)) if _passes(s))
    intents = tuple(name for _, _, name in reversed(ranked))[:MAX_INTENTS]
    return RoutePlan(_assemble(intents, bool(mentions), max_tools), intents, tuple(mentions))


def _assemble(intents: Sequence[str], has_players: bool, max_tools: int) -> list[str]:
    by_name = {i.name: i for i in INTENTS}
    groups: list[Sequence[str]] = [by_name[n].tools for n in intents]
    if has_players:      # the top topic first, then the core player tools, then the rest
        groups[1:1] = [PLAYER_TOOLS[:2]]
        groups.append(PLAYER_TOOLS[2:] if intents else PLAYER_TOOLS)
    if not groups:
        groups = [DEFAULT]
    chosen = list(ALWAYS)
    for group in groups:
        chosen += [t for t in group if t not in chosen]
    return chosen[:max(max_tools, len(ALWAYS))]


def route(text: str, player_names: Iterable[str] = ()) -> list[str]:
    """Compatibility wrapper: tool names for one block of text, matching the given full names."""
    names = tuple(player_names)
    return route_plan(text, PlayerIndex(names) if names else None).tools
