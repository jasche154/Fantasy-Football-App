"""Simulated seasons: the real scoring plays sim_closing_fit fits the closing window and overtime on (re-exported
there), read once with nflreadpy and cached in PLAYS_CACHE (git-ignored; the network is needed the first time, with
--refresh, or for a season not in it)."""
import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path

from integrations.weekly_sources import TEAM_ALIASES
from services.sim_script_fit import atomic_write

log = logging.getLogger(__name__)

PLAYS_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "scoring_plays.json"


def _band(distance: float | None) -> str:
    d = distance or 0
    return "0_39" if d < 40 else "40_49" if d < 50 else "50"


def _kind(r: Mapping) -> tuple[str, str] | None:
    """(team, event) of one play, or None when it scores nothing the box score keeps."""
    if r["touchdown"] == 1 and r["td_team"]:
        kind = "pass_td" if r["pass_touchdown"] == 1 else "rush_td" if r["rush_touchdown"] == 1 else "ret_td"
        return r["td_team"], kind
    if r["field_goal_result"] in ("made", "missed", "blocked"):
        return r["posteam"], ("fg_" if r["field_goal_result"] == "made" else "miss_") + _band(r["kick_distance"])
    if r["extra_point_result"] in ("good", "failed", "blocked"):
        return r["posteam"], "xp" if r["extra_point_result"] == "good" else "xp_miss"
    if r["two_point_conv_result"] == "success":
        return r["posteam"], "two"
    if r["safety"] == 1:
        return r["defteam"], "safety"
    return None


def _download(season: int) -> list[dict]:
    """One season's regular-season games: teams, final scores and every scoring play (quarter, seconds left,
    team, event, yards gained)."""
    import nflreadpy as nfl
    import polars as pl

    cols = ["game_id", "week", "home_team", "away_team", "home_score", "away_score", "qtr", "game_seconds_remaining",
            "posteam", "defteam", "touchdown", "td_team", "pass_touchdown", "rush_touchdown", "field_goal_result",
            "kick_distance", "extra_point_result", "two_point_conv_result", "safety", "yards_gained"]
    pbp = nfl.load_pbp(season).filter(pl.col("season_type") == "REG").select(cols)
    return games_of(pbp.iter_rows(named=True), season)


def games_of(rows: Iterable[Mapping], season: int) -> list[dict]:
    """The games of one season's play-by-play rows (see _download); a scoring play with no game clock is left
    out, counted and logged at WARNING (its window cannot be told)."""
    games: dict[str, dict] = {}
    no_clock = 0
    for r in rows:
        g = games.setdefault(r["game_id"], {"week": r["week"], "home": TEAM_ALIASES.get(r["home_team"], r["home_team"]),
                                            "away": TEAM_ALIASES.get(r["away_team"], r["away_team"]),
                                            "home_score": r["home_score"], "away_score": r["away_score"], "plays": []})
        kind = _kind(r)
        if kind is not None and r["game_seconds_remaining"] is None:
            no_clock += 1
        elif kind is not None:
            team = TEAM_ALIASES.get(kind[0], kind[0])
            g["plays"].append([int(r["qtr"]), int(r["game_seconds_remaining"]), team, kind[1],
                               int(r["yards_gained"] or 0)])
    if no_clock:
        log.warning("%s: %d scoring plays with no game clock left out", season, no_clock)
    return [{"season": season, **g} for g in games.values()]


def scoring_games(seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """Every real game of `seasons` with its scoring plays, from PLAYS_CACHE (downloaded when missing)."""
    wanted = sorted(set(seasons))
    cached = _cached()
    missing = [s for s in wanted if refresh or str(s) not in cached]        # a refresh keeps the other seasons
    if missing:
        for season in missing:
            log.info("downloading %s play-by-play", season)
            cached[str(season)] = _download(season)
        PLAYS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(PLAYS_CACHE, json.dumps(cached))
    return [g for s in wanted for g in cached[str(s)]]


def _cached() -> dict:
    try:
        cached = json.loads(PLAYS_CACHE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("%s is unreadable: downloading the play-by-play again", PLAYS_CACHE.name, exc_info=True)
        return {}
    if isinstance(cached, dict) and all(isinstance(v, list) for v in cached.values()):
        return cached
    log.warning("%s is not a {season: games} file: downloading the play-by-play again", PLAYS_CACHE.name)
    return {}
