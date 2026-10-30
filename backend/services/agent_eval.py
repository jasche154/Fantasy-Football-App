"""Evaluation set for Ask AI: saved questions, the tools each should use, and checks on the answer.

Two levels:
- Offline (pytest, tests/test_agent_routing.py): intent routing must send every case's
  expected tools. No model or network involved.
- Live (needs GROQ_API_KEY; uses free-tier tokens): runs each question through the real
  assistant and scores tool choice, grounding (no players a tool didn't return) and errors.
    python -m services.agent_eval                 # every case
    python -m services.agent_eval --only trade    # cases whose id contains "trade"

Run the live set after changing the prompt, the tools or the routing.
"""
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argparse  # noqa: E402
import logging
import time
from dataclasses import dataclass

log = logging.getLogger(__name__)
PAUSE_SECONDS = 20          # between live cases, to stay under the free tier's tokens per minute


@dataclass(frozen=True)
class Case:
    id: str
    question: str
    expect_any: tuple[str, ...]          # at least one of these tools should be called
    must_not_ask_roster: bool = False    # the answer must not ask the user to list players


CASES: tuple[Case, ...] = (
    Case("my-team-start", "Who should I start this week?", ("get_my_team", "get_my_matchup"), True),
    Case("my-team-flex", "Should I flex my WR3 or my RB3?", ("get_my_team", "get_weekly_rankings"), True),
    Case("matchup-win", "Am I going to win my matchup this week?", ("get_my_matchup",), True),
    Case("matchup-opponent", "Who's my opponent starting?", ("get_my_matchup",), True),
    Case("trade-league", "What trades can I make in my league for a running back?", ("find_league_trades",), True),
    Case("trade-eval", "Should I trade Bijan Robinson for Ja'Marr Chase?", ("evaluate_trade",)),
    Case("trade-anyone", "Find me trade ideas", ("find_league_trades", "find_trades"), True),
    Case("trade-rosters", "Which team in my league needs a quarterback?", ("get_league_rosters", "find_league_trades")),
    Case("waiver-league", "Who should I pick up on waivers?", ("get_league_waivers",), True),
    Case("waiver-position", "Best tight end to add this week?", ("get_league_waivers", "get_waiver_targets")),
    Case("waiver-faab", "How much FAAB should I bid on the top waiver RB?", ("get_league_waivers", "get_waiver_targets")),
    Case("playoffs-outlook", "How does my team look for the playoffs?", ("get_playoff_outlook",), True),
    Case("playoffs-sos", "Which receivers have the easiest playoff schedule?", ("get_schedule_strength",)),
    Case("sos-week", "Which defenses are easiest for RBs this week?", ("get_schedule_strength",)),
    Case("rankings-ros", "Top 10 rest of season wide receivers?", ("get_fantasy_rankings",)),
    Case("rankings-week", "Top QBs this week?", ("get_weekly_rankings",)),
    Case("leaderboard", "Who leads the league in RYOE per attempt?", ("get_leaderboard", "list_leaderboard_metrics")),
    Case("buy-low", "Give me some buy low candidates", ("get_buy_low_candidates",)),
    Case("sell-high", "Who's a sell high at receiver?", ("get_sell_high_candidates",)),
    Case("compare", "Compare Jahmyr Gibbs and Bijan Robinson", ("compare_players", "get_player_profile")),
    Case("explain", "Why is Puka Nacua projected so high this week?", ("explain_projection",)),
    Case("injury", "Is Breece Hall playing this week? He's questionable", ("explain_projection", "get_weekly_rankings")),
    Case("game-log", "How has Jahmyr Gibbs done in his last 3 games?", ("get_player_game_log", "get_player_profile")),
    Case("trending", "Who's trending on Sleeper?", ("get_trending_players",)),
    Case("other-team", "What does the best team in my league have on its roster?", ("get_league_rosters",)),
    Case("power-rankings", "Who are the real contenders in my league?", ("get_power_rankings",)),
    Case("playoff-odds", "What are my odds to make the playoffs?", ("get_power_rankings",)),
    Case("tendencies", "Which managers in my league actually make trades?", ("get_power_rankings",)),
    Case("news", "Any news on Lamar Jackson's ankle?", ("get_player_news",)),
    Case("playoff-fix", "Who should I pick up or trade for to fix my playoff lineup?", ("get_playoff_outlook",), True),
    Case("remember", "I'm 5-1 and my priority is the playoffs. Who should I target?", ("remember",)),
)
ROSTER_ASKS = ("which players", "list your", "who is on your", "who's on your", "share your roster",
               "tell me your roster", "provide your roster")


def score_live(case: Case, reply: str, tools: list[str], ok: list[bool]) -> list[str]:
    """Problems with one live answer (empty = pass)."""
    problems = []
    if not set(case.expect_any) & set(tools):
        problems.append(f"expected one of {list(case.expect_any)}, called {tools or 'nothing'}")
    if not reply.strip():
        problems.append("empty reply")
    if case.must_not_ask_roster and any(p in reply.lower() for p in ROSTER_ASKS):
        problems.append("asked the user for their roster")
    if tools and not any(ok):
        problems.append("every tool call failed")
    return problems


def run_live(only: str | None = None) -> int:
    from config import get_settings
    from database.db import SessionLocal
    from services.agent import GroqClient, run_agent

    settings = get_settings()
    if not settings.groq_api_key:
        log.error("GROQ_API_KEY isn't set in backend/.env; the live eval needs it.")
        return 2
    client = GroqClient(settings.groq_api_key, settings.groq_model)
    cases = [c for c in CASES if only is None or only in c.id]
    failures = 0
    for index, case in enumerate(cases):
        if index:
            time.sleep(PAUSE_SECONDS)
        with SessionLocal() as session:
            try:
                result = run_agent(session, client, [{"role": "user", "content": case.question}])
                problems = score_live(case, result.reply, [t.name for t in result.tool_calls],
                                      [t.ok for t in result.tool_calls])
            except Exception as exc:          # report and keep going; one bad case shouldn't stop the run
                problems = [f"{type(exc).__name__}: {exc}"]
        failures += bool(problems)
        log.info("%s %s%s", "PASS" if not problems else "FAIL", case.id, "" if not problems else f": {problems}")
    log.info("%d/%d passed", len(cases) - failures, len(cases))
    return 1 if failures else 0


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", help="run cases whose id contains this text")
    args = parser.parse_args(argv)
    raise SystemExit(run_live(args.only))


if __name__ == "__main__":
    main()
