"""Your Yahoo league as a whole: power rankings with playoff odds, and each manager's tendencies.

Power rankings
- Strength: each team's best lineup of QB/RB/WR/TE by rest-of-season points per game
  (kickers and defenses aren't projected, the same for every team).
- Playoff odds: the rest of the regular season is simulated SIMULATIONS times from Yahoo's
  real remaining schedule. Each week a team scores Normal(strength, WEEKLY_SD); wins and
  points for are added to the current standings, and the top playoff_teams (wins, then
  points for) make it. The weekly spread is what one team's total typically varies by.

Tendencies (from the season's transactions and Yahoo's move/trade counts)
- how often each manager trades and works the waiver wire, which positions they trade for
  and pick up, and a short label: "Active trader", "Waiver regular" or "Quiet".
  Trade offers are worth sending to active traders first.

Everything is read live from Yahoo and cached for CACHE_SECONDS per league.
"""
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from threading import Lock
from types import SimpleNamespace

import httpx
import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from config import Settings
from database.models import Team
from integrations import yahoo
from services import my_team as mt
from services import yahoo_sync
from services.playoffs import playoff_weeks
from services.valuation import build_valuation

SIMULATIONS = 10_000
WEEKLY_SD = 25.0                 # one team's weekly total: typical spread around its projection
CACHE_SECONDS = 3600
TRANSACTIONS_PAGE = 200
ACTIVE_TRADER_TRADES = 2
WAIVER_REGULAR_FACTOR = 1.5      # moves vs the league median
QUIET_MOVES = 2
TOP_POSITIONS = 2

_cache: dict[int, tuple[float, dict]] = {}
_lock = Lock()


def lineup_strength(valuation, settings, spots: Sequence[mt.RosterSpot]) -> float:
    """Rest-of-season points per game of the team's best QB/RB/WR/TE lineup."""
    players = [SimpleNamespace(player_id=v.player_id, position=v.position, projection=v.ros_ppg)
               for s in spots if s.slot not in ("IR", "IR+", "NA")
               and (v := valuation.by_id.get(s.player_id)) is not None and v.position in mt.MODELED]
    best = mt.best_lineup(players, settings)
    by_id = {p.player_id: p.projection for p in players}
    return round(sum(by_id[pid] for ids in best.values() for pid in ids), 1)


def simulate_season(teams: Sequence[str], strength: Mapping[str, float], wins: Mapping[str, float],
                    points_for: Mapping[str, float], games: Sequence[tuple[str, str]], playoff_teams: int,
                    sims: int = SIMULATIONS, seed: int = 11) -> dict[str, dict]:
    """{team: {playoff_odds, expected_wins, top_seed_odds}} over the remaining games."""
    rng = np.random.default_rng(seed)
    index = {t: i for i, t in enumerate(teams)}
    total_wins = np.tile(np.array([wins.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    total_points = np.tile(np.array([points_for.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    means = np.array([strength.get(t, 0.0) for t in teams])
    for home, away in games:
        if home not in index or away not in index:
            continue
        a, b = index[home], index[away]
        score_a = rng.normal(means[a], WEEKLY_SD, sims)
        score_b = rng.normal(means[b], WEEKLY_SD, sims)
        total_wins[:, a] += score_a > score_b
        total_wins[:, b] += score_b > score_a
        total_points[:, a] += score_a
        total_points[:, b] += score_b
    # Seeds: wins first, points for breaks ties (scaled far below one win).
    order = np.argsort(-(total_wins * 1e6 + total_points), axis=1)
    seeds = np.empty_like(order)
    seeds[np.arange(sims)[:, None], order] = np.arange(len(teams))
    cutoff = min(max(playoff_teams, 1), len(teams))
    return {t: {"playoff_odds": round(float((seeds[:, i] < cutoff).mean()), 3),
                "top_seed_odds": round(float((seeds[:, i] == 0).mean()), 3),
                "expected_wins": round(float(total_wins[:, i].mean()), 1)}
            for t, i in index.items()}


def tendencies(standings: Sequence[dict], transactions: Sequence[dict]) -> dict[str, dict]:
    """{team_key: {moves, trades, label, trades_for, trades_away, picks_up}}."""
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
        out[key] = {"moves": s["moves"], "trades": s["trades"], "label": label,
                    "trades_for": [p for p, _ in traded_in.get(key, Counter()).most_common(TOP_POSITIONS)],
                    "trades_away": [p for p, _ in traded_out.get(key, Counter()).most_common(TOP_POSITIONS)],
                    "picks_up": [p for p, _ in added.get(key, Counter()).most_common(TOP_POSITIONS)]}
    return out


def _remaining_games(http: httpx.Client, token, league_key: str, first: int, last: int) -> list[tuple[str, str]]:
    games = []
    for week in range(first, last + 1):
        board = yahoo.parse_scoreboard(yahoo.api_get(http, token, f"league/{league_key}/scoreboard;week={week}"))
        for matchup in board:
            keys = [t["team_key"] for t in matchup["teams"]]
            if len(keys) == 2:
                games.append((keys[0], keys[1]))
    return games


def build(session: Session, settings: Settings, http: httpx.Client, reference: int | str | None) -> dict:
    league, mine = mt.find_league(session, reference)
    with _lock:
        cached = _cache.get(league.id)
    if cached and time.time() - cached[0] < CACHE_SECONDS:
        return cached[1]
    token = yahoo_sync.fresh_token(session, settings, http)
    key = league.platform_league_id
    standings = yahoo.parse_standings(yahoo.api_get(http, token, f"league/{key}/standings"))
    transactions = yahoo.parse_transactions(
        yahoo.api_get(http, token, f"league/{key}/transactions;types=add,trade;count={TRANSACTIONS_PAGE}"))
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
    wins = {s["team_key"]: s["wins"] + 0.5 * s["ties"] for s in standings}
    points = {s["team_key"]: s["points_for"] for s in standings}
    keys = [s["team_key"] for s in standings]
    playoff_teams = int(rules.get("playoff_teams") or (6 if len(keys) >= 10 else 4))     # Yahoo's defaults
    odds = simulate_season(keys, strength, wins, points, games, playoff_teams)
    habits = tendencies(standings, transactions)
    remaining_opponents: dict[str, list[float]] = {k: [] for k in keys}
    for home, away in games:
        if home in remaining_opponents and away in strength:
            remaining_opponents[home].append(strength[away])
        if away in remaining_opponents and home in strength:
            remaining_opponents[away].append(strength[home])

    rows = []
    for s in standings:
        team_key = s["team_key"]
        opponents = remaining_opponents.get(team_key) or []
        rows.append({"team": s["name"], "team_key": team_key, "is_users_team": team_key == mine.platform_team_id,
                     "record": f"{s['wins']}-{s['losses']}" + (f"-{s['ties']}" if s["ties"] else ""),
                     "standing": s["rank"], "points_for": round(s["points_for"], 1),
                     "points_against": round(s["points_against"], 1), "strength": strength.get(team_key),
                     "remaining_opponent_strength": round(sum(opponents) / len(opponents), 1) if opponents else None,
                     **odds.get(team_key, {}), "tendencies": habits.get(team_key)})
    rows.sort(key=lambda r: -(r["strength"] or 0))
    for rank, row in enumerate(rows, start=1):
        row["power_rank"] = rank
    result = {"league": league.name, "playoff_teams": playoff_teams, "regular_season_weeks_left": len(
        {w for w in range(games_played + 1, weeks[0])}), "simulations": SIMULATIONS, "teams": rows}
    with _lock:
        _cache[league.id] = (time.time(), result)
    return result
