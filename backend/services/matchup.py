"""This week's head-to-head matchup: both lineups, win probability, upside vs safe.

- Opponent and live scores come from the Yahoo scoreboard; both rosters from the last import.
- Each starter's score is simulated SIMULATIONS times: a normal draw around the projection,
  with spread from his floor / ceiling (10th / 90th percentile, so sd = (ceiling - floor) / 2.56),
  a 0 when he sits (his chance to play), and his actual points once his game is final.
  Win probability = the share of simulated weeks your total beats the opponent's.
- Upside vs safe: three lineups that fill your slots by a different score each
      expected  = projection
      upside    = projection + UPSIDE_WEIGHT x (ceiling - projection)
      safe      = projection - SAFE_WEIGHT x (projection - floor)
  are each simulated against the opponent's lineup, and the one that wins most often is
  recommended. A big underdog usually wins more with upside; a big favorite with safe floors.
"""
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

import httpx
import numpy as np
from sqlalchemy.orm import Session

from config import Settings
from integrations import yahoo
from services import my_team as mt
from services import yahoo_sync
from services.weekly_rankings import WeeklyPlayer

SIMULATIONS = 20_000
UPSIDE_WEIGHT = 0.5
SAFE_WEIGHT = 0.5
Z90 = 1.2816                      # the 90th percentile of a standard normal
MODES = ("expected", "upside", "safe")


@dataclass(frozen=True)
class Side:
    team_name: str
    team_key: str
    starters: list[tuple[str, int]]          # (slot, player id)
    bench: list[int]
    yahoo_points: float | None
    yahoo_projected: float | None
    yahoo_win_probability: float | None


def _score(mode: str) -> Callable[[WeeklyPlayer], float]:
    def floor(p: WeeklyPlayer) -> float:
        return p.floor if p.floor is not None else p.projection

    def ceiling(p: WeeklyPlayer) -> float:
        return p.ceiling if p.ceiling is not None else p.projection

    if mode == "upside":
        return lambda p: p.projection + UPSIDE_WEIGHT * (ceiling(p) - p.projection)
    if mode == "safe":
        return lambda p: p.projection - SAFE_WEIGHT * (p.projection - floor(p))
    return lambda p: p.projection


def simulate_totals(players: Sequence[WeeklyPlayer], rng: np.random.Generator, n: int = SIMULATIONS) -> np.ndarray:
    """n simulated weekly totals for a lineup."""
    totals = np.zeros(n)
    for p in players:
        if p.actual is not None:
            totals += p.actual                          # already played
            continue
        mean = p.if_active or p.projection
        floor = p.floor if p.floor is not None else mean * 0.5
        ceiling = p.ceiling if p.ceiling is not None else mean * 1.5
        sd = max(0.5, (ceiling - floor) / (2 * Z90))
        draws = rng.normal(mean, sd, n)
        if p.position not in ("DST",):
            draws = np.maximum(draws, 0.0)
        plays = rng.random(n) < (p.play_prob if p.play_prob is not None else 1.0)
        totals += np.where(plays, draws, 0.0)
    return totals


def win_probability(mine: Sequence[WeeklyPlayer], theirs: Sequence[WeeklyPlayer], seed: int = 7) -> float:
    rng = np.random.default_rng(seed)
    a, b = simulate_totals(mine, rng), simulate_totals(theirs, rng)
    return float(np.mean(a > b) + 0.5 * np.mean(a == b))


def _side(session: Session, league, team_meta: dict, rosters: Mapping[int, Sequence[mt.RosterSpot]],
          team_ids: Mapping[str, int]) -> Side:
    team_id = team_ids.get(team_meta["team_key"])
    spots = rosters.get(team_id, []) if team_id else []
    return Side(team_name=team_meta.get("name") or "", team_key=team_meta["team_key"],
                starters=[(s.slot, s.player_id) for s in spots if s.starting],
                bench=[s.player_id for s in spots if not s.starting],
                yahoo_points=team_meta.get("points"), yahoo_projected=team_meta.get("projected"),
                yahoo_win_probability=team_meta.get("win_probability"))


def build_matchup(session: Session, settings: Settings, http: httpx.Client, reference: int | str | None) -> dict:
    from sqlalchemy import select

    from database.models import Team
    from services.kdst_rankings import weekly_players

    team = mt.build_my_team(session, reference)
    league = team.league
    token = yahoo_sync.fresh_token(session, settings, http)
    week = team.rankings.week
    board = yahoo.parse_scoreboard(yahoo.api_get(http, token, f"league/{league.platform_league_id}/scoreboard;week={week}"))
    mine_meta, theirs_meta = None, None
    for matchup in board:
        keys = [t["team_key"] for t in matchup["teams"]]
        if team.team.platform_team_id in keys:
            mine_meta = next(t for t in matchup["teams"] if t["team_key"] == team.team.platform_team_id)
            theirs_meta = next((t for t in matchup["teams"] if t["team_key"] != team.team.platform_team_id), None)
    if mine_meta is None or theirs_meta is None:
        raise LookupError(f"No matchup this week for {team.team.name} (bye or playoffs not reached).")
    team_ids = {t.platform_team_id: t.id for t in session.scalars(select(Team).where(Team.league_id == league.id))}
    rosters = mt.rosters(session, league)
    mine, theirs = (_side(session, league, m, rosters, team_ids) for m in (mine_meta, theirs_meta))
    rules = mt.league_rules(league)
    weekly = team.weekly()
    their_ids = [pid for _, pid in theirs.starters] + theirs.bench
    weekly.update(weekly_players(session, team.rankings.season, week, their_ids,
                                 rules.get("k_scoring"), rules.get("dst_scoring")))

    def lineup(ids: Sequence[int]) -> list[WeeklyPlayer]:
        return [weekly[pid] for pid in ids if pid in weekly]

    their_lineup = lineup([pid for _, pid in theirs.starters])
    current = lineup([pid for _, pid in mine.starters])
    available = {s.player_id for s in team.spots if s.slot not in ("IR", "IR+", "NA")}
    my_players = [weekly[s.player_id] for s in team.spots if s.player_id in weekly]
    options = {}
    for mode in MODES:
        slots = mt.best_lineup(my_players, team.settings, available, key=_score(mode))
        ids = [pid for slot_ids in slots.values() for pid in slot_ids]
        options[mode] = {"lineup": slots, "projected": round(sum(weekly[pid].projection for pid in ids), 1),
                         "win_probability": round(win_probability(lineup(ids), their_lineup), 3)}
    recommended = max(MODES, key=lambda m: (options[m]["win_probability"], m == "expected"))
    current_ids = {pid for _, pid in mine.starters}
    rec_ids = {pid for ids in options[recommended]["lineup"].values() for pid in ids}
    names = {pid: p.name for pid, p in weekly.items()}

    def players(side: Side) -> list[dict]:
        rows = []
        for slot, pid in side.starters:
            p = weekly.get(pid)
            rows.append({"slot": slot, "player_id": pid, "name": names.get(pid) or mt.player_name(session, pid),
                         "position": p.position if p else None, "team": p.team if p else None,
                         "opponent": p.matchup.opponent if p else None,
                         "projection": p.projection if p else 0.0, "floor": p.floor if p else None,
                         "ceiling": p.ceiling if p else None, "actual": p.actual if p else None,
                         "status": p.status if p else "no game / out"})
        return rows

    return {
        "league": {"id": league.id, "name": league.name}, "week": week,
        "me": {"team": mine.team_name, "starters": players(mine), "projected": round(sum(p.projection for p in current), 1),
               "yahoo_points": mine.yahoo_points, "yahoo_projected": mine.yahoo_projected},
        "opponent": {"team": theirs.team_name, "starters": players(theirs),
                     "projected": round(sum(p.projection for p in their_lineup), 1),
                     "yahoo_points": theirs.yahoo_points, "yahoo_projected": theirs.yahoo_projected},
        "win_probability": round(win_probability(current, their_lineup), 3),
        "yahoo_win_probability": mine.yahoo_win_probability,
        "strategies": {mode: {"projected": o["projected"], "win_probability": o["win_probability"]}
                       for mode, o in options.items()},
        "recommended": recommended,
        "changes": {"start": [names.get(pid) for pid in rec_ids - current_ids],
                    "sit": [names.get(pid) or mt.player_name(session, pid) for pid in current_ids - rec_ids]},
        "simulations": SIMULATIONS,
    }
