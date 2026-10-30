"""Your Yahoo league as a whole: power rankings with playoff odds, and each manager's tendencies.

Power rankings
- Strength: each team's best lineup of QB/RB/WR/TE by rest-of-season points per game
  (kickers and defenses aren't projected, the same for every team).
- Weekly projection (best ball): for every remaining week, each team's best lineup from its
  whole roster (not IR), each player projected against that week's opponent:
      rest-of-season points per game x (opponent defense factor)^ROS_SCHEDULE_WEIGHT
  A player on bye is out that week. For each player a team has on bye, it also gets the
  week's best free agent at his position (the best unrostered player in the league, not on
  bye) as a rough waiver pickup, so a bye week costs only the gap to the waiver wire.
- Playoff odds: the rest of the regular season is simulated SIMULATIONS times from Yahoo's
  real remaining schedule. In each simulated season:
    * every team gets one strength offset, Normal(0, SEASON_SD): rest-of-season estimates miss
      by about this much, and the miss lasts all season (it doesn't average out week to week);
    * every starter can get hurt: each week he misses with his position's hazard (QB 8.8%,
      WR 5.4%, RB/TE 4.3%), and once out stays out the next week 60% of the time; a player
      already Out starts out, one on IR misses IR_WEEKS. His spot goes to the best free agent;
    * each game a team scores Normal(that week's projection + offset - injury losses, WEEKLY_SD).
  Wins and points for are added to the current standings, and the top playoff_teams (wins,
  then points for) make it. Projections use healthy rates, since injuries are simulated.

Tendencies (from the season's transactions and Yahoo's move/trade counts)
- how often each manager trades and works the waiver wire, which positions they trade for
  and pick up, and a short label: "Active trader", "Waiver regular" or "Quiet".
  Trade offers are worth sending to active traders first.

Everything is read live from Yahoo and cached for CACHE_SECONDS per league.
"""
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Mapping, Sequence
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
from services.build_locks import key_lock
from services.playoffs import playoff_weeks
from services.valuation import ROS_SCHEDULE_WEIGHT, build_valuation

SIMULATIONS = 10_000
WEEKLY_SD = 21.0                 # one team's weekly total around its projection (skill positions, 2021-25)
SEASON_SD = 9.0                  # how far a team's rest-of-season strength estimate misses, all season
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


def weekly_strengths(rosters: Mapping[str, Sequence], free_agents: Sequence, weeks: Sequence[int], settings,
                     project: Callable[[object, int], float | None]) -> dict[str, dict[int, float]]:
    """{team: {week: best-ball lineup projection}} (see the module docstring)."""
    lineups = weekly_lineups(rosters, free_agents, weeks, settings, project)
    return {team: {week: round(sum(points for _, _, points, _ in starters), 1) for week, starters in by_week.items()}
            for team, by_week in lineups.items()}


def weekly_lineups(rosters: Mapping[str, Sequence], free_agents: Sequence, weeks: Sequence[int], settings,
                   project: Callable[[object, int], float | None]) -> dict[str, dict[int, list[tuple]]]:
    """{team: {week: [(player id, position, points, waiver replacement points)]}} for each starter.

    rosters hold each team's player values (IR excluded); project(value, week) is that week's
    projection, or None on a bye. Waiver fill-ins (for byes) have negative ids.
    """
    waiver: dict[tuple[str, int], float] = {}
    for value in free_agents:
        for week in weeks:
            points = project(value, week)
            if points is not None and points > waiver.get((value.position, week), 0.0):
                waiver[(value.position, week)] = points
    out: dict[str, dict[int, list[tuple]]] = {}
    for team, values in rosters.items():
        out[team] = {}
        for week in weeks:
            pool, byes = [], Counter()
            for value in values:
                points = project(value, week)
                if points is None:
                    byes[value.position] += 1
                else:
                    pool.append(SimpleNamespace(player_id=value.player_id, position=value.position, projection=points))
            for position, count in byes.items():          # one rough pickup per player on bye
                pool += [SimpleNamespace(player_id=-(len(pool) + i + 1), position=position,
                                         projection=waiver.get((position, week), 0.0)) for i in range(count)]
            best = mt.best_lineup(pool, settings)
            by_id = {p.player_id: p for p in pool}
            out[team][week] = [(pid, by_id[pid].position, by_id[pid].projection,
                                waiver.get((by_id[pid].position, week), 0.0)) for ids in best.values() for pid in ids]
    return out


def _start_out(session: Session) -> dict[int, int]:
    """{player id: weeks certainly out}: IR-type tags IR_WEEKS, Out now 1 (Sleeper's live status)."""
    from database.models import PlayerStatus
    from services.lineup_gain import IR_STATUSES, IR_WEEKS

    out = {}
    for status in session.scalars(select(PlayerStatus).where(PlayerStatus.injury_status.is_not(None))):
        tag = (status.injury_status or "").upper()
        if tag in IR_STATUSES:
            out[status.player_id] = IR_WEEKS
        elif tag == "OUT":
            out[status.player_id] = 1
    return out


def _active_values(valuation, spots: Sequence[mt.RosterSpot]) -> list:
    return [v for s in spots if s.slot not in ("IR", "IR+", "NA")
            and (v := valuation.by_id.get(s.player_id)) is not None and v.position in mt.MODELED and v.team]


def _free_agents(valuation, rosters: Mapping[int, Sequence[mt.RosterSpot]]) -> list:
    rostered = {s.player_id for spots in rosters.values() for s in spots}
    return [v for v in valuation.values if v.position in mt.MODELED and v.team and v.player_id not in rostered]


def _projector(valuation) -> Callable[[object, int], float | None]:
    """Healthy rate x matchup (the season simulation adds injuries itself)."""
    schedule, defense = valuation.schedule, valuation.defense

    def project(value, week: int) -> float | None:
        opponent = schedule.opponent(value.team, week)
        if opponent is None:
            return None                                   # bye
        share = getattr(value, "availability", 1.0) or 1.0
        rate = value.ros_ppg / share if share > 0.05 else value.ros_ppg
        return rate * defense.factor(opponent, value.position) ** ROS_SCHEDULE_WEIGHT
    return project


def injury_losses(lineups: Mapping[int, list[tuple]], start_out: Mapping[int, int], rng: np.random.Generator,
                  sims: int) -> dict[int, np.ndarray]:
    """{week: (sims,) points lost to starter injuries} for one team (see the module docstring).

    start_out: {player id: weeks he's certainly out (IR) or 1 (Out now)}.
    """
    from services.lineup_gain import HAZARD, OUT_PERSIST

    weeks = sorted(lineups)
    players = {pid: pos for week in weeks for pid, pos, _, _ in lineups[week] if pid > 0}
    out = {pid: np.full(sims, start_out.get(pid, 0) > 0) for pid in players}
    losses = {}
    for index, week in enumerate(weeks):
        if index:
            for pid, pos in players.items():
                forced = start_out.get(pid, 0) > index
                stays = rng.random(sims) < OUT_PERSIST
                hurt = rng.random(sims) < HAZARD.get(pos, 0.05)
                out[pid] = forced | np.where(out[pid], stays, hurt)
        loss = np.zeros(sims)
        for pid, _, points, replacement in lineups[week]:
            if pid > 0:
                loss += out[pid] * max(points - replacement, 0.0)
        losses[week] = loss
    return losses


def simulate_season(teams: Sequence[str], strength: Mapping[str, Mapping[int, float]], wins: Mapping[str, float],
                    points_for: Mapping[str, float], games: Sequence[tuple[int, str, str]], playoff_teams: int,
                    sims: int = SIMULATIONS, seed: int = 11, season_sd: float = SEASON_SD,
                    losses: Mapping[str, Mapping[int, np.ndarray]] | None = None) -> dict[str, dict]:
    """{team: {playoff_odds, expected_wins, top_seed_odds}} over the remaining (week, home, away) games:
    each team scores around its projection for that week, plus its season offset, minus injury losses."""
    rng = np.random.default_rng(seed)
    index = {t: i for i, t in enumerate(teams)}
    total_wins = np.tile(np.array([wins.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    total_points = np.tile(np.array([points_for.get(t, 0.0) for t in teams], dtype=float), (sims, 1))
    offsets = rng.normal(0.0, season_sd, (sims, len(teams))) if season_sd else np.zeros((sims, len(teams)))
    losses = losses or {}

    def mean(team: str, week: int) -> np.ndarray:
        lost = losses.get(team, {}).get(week)
        return strength.get(team, {}).get(week, 0.0) + offsets[:, index[team]] - (lost if lost is not None else 0.0)

    for week, home, away in games:
        if home not in index or away not in index:
            continue
        a, b = index[home], index[away]
        score_a = rng.normal(mean(home, week), WEEKLY_SD)
        score_b = rng.normal(mean(away, week), WEEKLY_SD)
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


_schedule: dict[tuple[str, int], list[tuple[str, str]]] = {}      # (league key, week): pairings; fixed all season
SCHEDULE_WORKERS = 6


def _remaining_games(http: httpx.Client, token, league_key: str, first: int, last: int) -> list[tuple[int, str, str]]:
    """Every remaining (week, team, team) pairing. Pairings don't change, so each week is fetched
    once per run of the app, and missing weeks are fetched in parallel."""
    def fetch(week: int) -> tuple[int, list[tuple[str, str]]]:
        board = yahoo.parse_scoreboard(yahoo.api_get(http, token, f"league/{league_key}/scoreboard;week={week}"))
        return week, [(m["teams"][0]["team_key"], m["teams"][1]["team_key"]) for m in board if len(m["teams"]) == 2]

    weeks = range(first, last + 1)
    with _lock:
        missing = [w for w in weeks if (league_key, w) not in _schedule]
    if missing:
        with ThreadPoolExecutor(max_workers=SCHEDULE_WORKERS) as pool:
            fetched = list(pool.map(fetch, missing))
        with _lock:
            for week, pairs in fetched:
                _schedule[(league_key, week)] = pairs
    with _lock:
        return [(w, a, b) for w in weeks for a, b in _schedule.get((league_key, w), [])]


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
    remaining_weeks = sorted({week for week, _, _ in games})
    lineups = weekly_lineups(
        {key: _active_values(valuation, rosters.get(team.id, [])) for key, team in teams.items()},
        _free_agents(valuation, rosters), remaining_weeks, league_settings, _projector(valuation))
    weekly = {team: {week: round(sum(points for _, _, points, _ in starters), 1) for week, starters in by_week.items()}
              for team, by_week in lineups.items()}
    start_out = _start_out(session)
    rng = np.random.default_rng(23)
    losses = {team: injury_losses(by_week, start_out, rng, SIMULATIONS) for team, by_week in lineups.items()}
    wins = {s["team_key"]: s["wins"] + 0.5 * s["ties"] for s in standings}
    points = {s["team_key"]: s["points_for"] for s in standings}
    keys = [s["team_key"] for s in standings]
    playoff_teams = int(rules.get("playoff_teams") or (6 if len(keys) >= 10 else 4))     # Yahoo's defaults
    odds = simulate_season(keys, weekly, wins, points, games, playoff_teams, losses=losses)
    habits = tendencies(standings, transactions)
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
                     "faab_balance": s.get("faab_balance"), "waiver_priority": s.get("waiver_priority")})
    rows.sort(key=lambda r: -(r["strength"] or 0))
    for rank, row in enumerate(rows, start=1):
        row["power_rank"] = rank
    result = {"league": league.name, "winning_bids_by_week": winning_bids(transactions),
              "playoff_teams": playoff_teams, "regular_season_weeks_left": len(
        {w for w in range(games_played + 1, weeks[0])}), "simulations": SIMULATIONS, "teams": rows}
    with _lock:
        _cache[league.id] = (time.time(), result)
    return result
