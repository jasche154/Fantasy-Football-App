"""Your Yahoo league as a whole: power rankings with playoff odds, and each manager's tendencies.

Power rankings and playoff odds: services/season_sim.py.

Tendencies (from the season's transactions and Yahoo's move/trade counts)
- how often each manager trades and works the waiver wire, which positions they trade for
  and pick up, and a short label: "Active trader", "Waiver regular" or "Quiet".
  Trade offers are worth sending to active traders first.
- tags with their evidence ("FAAB whale", "Waiver hawk", "RB hunter", ...): services/manager_tags.py.

All-play records and luck (from completed weeks' scoreboards): services/all_play.py.
Past trades, graded on the points scored since: services/trade_grades.py.

Everything is read live from Yahoo and cached for CACHE_SECONDS per league.
"""
import logging
import time
from collections import Counter
from collections.abc import Sequence
from threading import Lock

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings
from database.models import Team
from integrations import yahoo
from services import my_team as mt
from services import trade_grades, yahoo_sync
from services.all_play import all_play_rows
from services.build_locks import key_lock
from services.league_scoreboards import (  # noqa: F401  (re-exported)
    SCHEDULE_WORKERS, _schedule, _scores, remaining_games as _remaining_games, week_scores,
)
from services.manager_tags import manager_tags
from services.season_sim import (  # noqa: F401  (re-exported)
    SEASON_SD, SIMULATIONS, WEEKLY_SD, _active_values, _free_agents, _projector, _start_out, injury_losses,
    lineup_strength, simulate_season, weekly_lineups, weekly_strengths,
)
from services.playoffs import playoff_weeks
from services.valuation import build_valuation

CACHE_SECONDS = 3600
TRANSACTIONS_PAGE = 200
MAX_TRADE_PAGES = 10             # 2,000 trades: far more than any league makes in a season
ACTIVE_TRADER_TRADES = 2
WAIVER_REGULAR_FACTOR = 1.5      # moves vs the league median
QUIET_MOVES = 2
TOP_POSITIONS = 2

logger = logging.getLogger(__name__)
_cache: dict[int, tuple[float, dict]] = {}
_lock = Lock()


def winning_bids(transactions: Sequence[dict]) -> list[list[float]]:
    """Winning FAAB bids grouped by the week they cleared (waivers clear weekly), oldest first."""
    from datetime import datetime, timezone

    weeks: dict[tuple[int, int], list[float]] = {}
    for transaction in transactions:
        for move in transaction["moves"]:
            if move["type"] == "add" and move.get("faab_bid") and transaction.get("timestamp"):
                week = datetime.fromtimestamp(transaction["timestamp"], timezone.utc).isocalendar()[:2]
                weeks.setdefault(week, []).append(move["faab_bid"])
    return [weeks[w] for w in sorted(weeks)]


def tendencies(standings: Sequence[dict], transactions: Sequence[dict],
               tags: dict[str, list[dict]] | None = None) -> dict[str, dict]:
    """{team_key: {moves, trades, label, tags, trades_for, trades_away, picks_up}}."""
    traded_in: dict[str, Counter] = {s["team_key"]: Counter() for s in standings}
    traded_out: dict[str, Counter] = {s["team_key"]: Counter() for s in standings}
    added: dict[str, Counter] = {s["team_key"]: Counter() for s in standings}
    for transaction in transactions:
        for move in transaction["moves"]:
            position = move.get("position") or "?"
            if move["type"] == "trade":
                traded_in.setdefault(move["to_team"], Counter())[position] += 1
                traded_out.setdefault(move["from_team"], Counter())[position] += 1
            elif move["type"] == "add" and move.get("to_team"):
                added.setdefault(move["to_team"], Counter())[position] += 1
    moves = sorted(s["moves"] for s in standings) or [0]
    median = moves[len(moves) // 2]
    out = {}
    for s in standings:
        key = s["team_key"]
        if s["trades"] >= ACTIVE_TRADER_TRADES:
            label = "Active trader"
        elif s["moves"] >= max(QUIET_MOVES + 1, median * WAIVER_REGULAR_FACTOR):
            label = "Waiver regular"
        elif s["moves"] <= QUIET_MOVES and s["trades"] == 0:
            label = "Quiet"
        else:
            label = None
        out[key] = {"moves": s["moves"], "trades": s["trades"], "label": label, "tags": (tags or {}).get(key, []),
                    "trades_for": [p for p, _ in traded_in.get(key, Counter()).most_common(TOP_POSITIONS)],
                    "trades_away": [p for p, _ in traded_out.get(key, Counter()).most_common(TOP_POSITIONS)],
                    "picks_up": [p for p, _ in added.get(key, Counter()).most_common(TOP_POSITIONS)]}
    return out


def _transactions(http: httpx.Client, token, key: str) -> list[dict]:
    """The latest TRANSACTIONS_PAGE adds, plus every trade this season (paged, so the add volume
    can't push trades out of the grades and trade tags)."""
    adds = [t for t in yahoo.parse_transactions(
        yahoo.api_get(http, token, f"league/{key}/transactions;types=add;count={TRANSACTIONS_PAGE}"))
        if t.get("type") != "trade"]
    trades: list[dict] = []
    for page in range(MAX_TRADE_PAGES):
        batch = yahoo.parse_transactions(yahoo.api_get(
            http, token, f"league/{key}/transactions;types=trade;start={page * TRANSACTIONS_PAGE};count={TRANSACTIONS_PAGE}"))
        trades += [t for t in batch if t.get("type") == "trade"]
        if len(batch) < TRANSACTIONS_PAGE:
            break
    return adds + trades


def _luck(http: httpx.Client, token, key: str, games_played: int) -> dict[str, dict]:
    """All-play and luck per team; {} when Yahoo's scoreboards can't be read (the rankings still load)."""
    try:
        return all_play_rows(week_scores(http, token, key, games_played))
    except (httpx.HTTPError, yahoo.YahooError) as exc:
        logger.warning("All-play scoreboards unavailable (%s)", type(exc).__name__)
        return {}


def _habits(session: Session, league, rules: dict, standings: Sequence[dict], transactions: Sequence[dict],
            games_played: int) -> tuple[dict[str, dict], list[dict]]:
    """(tendencies with tags, graded past trades)."""
    names = {s["team_key"]: s["name"] for s in standings}
    past_trades = trade_grades.league_trade_grades(session, league, transactions, names)
    tags = manager_tags(standings, transactions, uses_faab=rules.get("uses_faab") is True,
                        budget=rules.get("faab_budget"), weeks_played=games_played,
                        records=trade_grades.trade_records(past_trades))
    return tendencies(standings, transactions, tags), past_trades


def build(session: Session, settings: Settings, http: httpx.Client, reference: int | str | None) -> dict:
    league, _ = mt.find_league(session, reference)
    with key_lock(("league_insights", league.id)):
        return _build(session, settings, http, reference)


def _build(session: Session, settings: Settings, http: httpx.Client, reference: int | str | None) -> dict:
    league, mine = mt.find_league(session, reference)
    with _lock:
        cached = _cache.get(league.id)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    token = yahoo_sync.fresh_token(session, settings, http)
    key = league.platform_league_id
    standings = yahoo.parse_standings(yahoo.api_get(http, token, f"league/{key}/standings"))
    transactions = _transactions(http, token, key)
    rules = mt.league_rules(league)
    league_settings = mt.league_settings(league)
    valuation = build_valuation(session, league_settings)
    weeks = playoff_weeks(rules)
    games_played = max((s["wins"] + s["losses"] + s["ties"] for s in standings), default=0)
    games = _remaining_games(http, token, key, games_played + 1, weeks[0] - 1)

    teams = {t.platform_team_id: t for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    rosters = mt.rosters(session, league)
    strength = {s["team_key"]: lineup_strength(valuation, league_settings, rosters.get(teams[s["team_key"]].id, []))
                for s in standings if s["team_key"] in teams}
    remaining_weeks = sorted({week for week, _, _ in games})
    lineups = weekly_lineups(
        {key: _active_values(valuation, rosters.get(team.id, [])) for key, team in teams.items()},
        _free_agents(valuation, rosters), remaining_weeks, league_settings, _projector(valuation))
    weekly = {team: {week: round(sum(points for _, _, points, _ in starters), 1) for week, starters in by_week.items()}
              for team, by_week in lineups.items()}
    start_out = _start_out(valuation)
    rng = np.random.default_rng(23)
    losses = {team: injury_losses(by_week, start_out, rng, SIMULATIONS) for team, by_week in lineups.items()}
    wins = {s["team_key"]: s["wins"] + 0.5 * s["ties"] for s in standings}
    points = {s["team_key"]: s["points_for"] for s in standings}
    keys = [s["team_key"] for s in standings]
    playoff_teams = int(rules.get("playoff_teams") or (6 if len(keys) >= 10 else 4))     # Yahoo's defaults
    odds = simulate_season(keys, weekly, wins, points, games, playoff_teams, losses=losses)
    habits, past_trades = _habits(session, league, rules, standings, transactions, games_played)
    luck = _luck(http, token, key, games_played)
    remaining_opponents: dict[str, list[float]] = {k: [] for k in keys}
    for week, home, away in games:
        if home in remaining_opponents and away in weekly:
            remaining_opponents[home].append(weekly[away][week])
        if away in remaining_opponents and home in weekly:
            remaining_opponents[away].append(weekly[home][week])

    rows = []
    for s in standings:
        team_key = s["team_key"]
        opponents = remaining_opponents.get(team_key) or []
        rows.append({"team": s["name"], "team_key": team_key, "is_users_team": team_key == mine.platform_team_id,
                     "record": f"{s['wins']}-{s['losses']}" + (f"-{s['ties']}" if s["ties"] else ""),
                     "standing": s["rank"], "points_for": round(s["points_for"], 1),
                     "points_against": round(s["points_against"], 1), "strength": strength.get(team_key),
                     "remaining_opponent_strength": round(sum(opponents) / len(opponents), 1) if opponents else None,
                     "weekly_projection": weekly.get(team_key, {}),
                     **odds.get(team_key, {}), "tendencies": habits.get(team_key),
                     **{k: v for k, v in luck.get(team_key, {}).items() if k != "weeks"},
                     "faab_balance": s.get("faab_balance"), "waiver_priority": s.get("waiver_priority")})
    rows.sort(key=lambda r: -(r["strength"] or 0))
    for rank, row in enumerate(rows, start=1):
        row["power_rank"] = rank
    result = {"league": league.name, "winning_bids_by_week": winning_bids(transactions),
              "playoff_teams": playoff_teams, "regular_season_weeks_left": max(0, weeks[0] - 1 - games_played), "simulations": SIMULATIONS, "teams": rows,
              "past_trades": past_trades}
    with _lock:
        _cache[league.id] = (time.time(), result)
    return result
