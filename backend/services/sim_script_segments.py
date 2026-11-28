"""Simulated seasons: the real play-by-play counts sim_script_fit fits on, per team-game and part (first half, second
half to the closing window's cut, the window), cached in SEGMENTS_CACHE (git-ignored; downloaded the first time,
with --refresh, or for a season missing or cached in another column layout). sim_script_fit re-exports these, and
sim_closing_fit reads the window's counts through it. See sim_script_fit's docstring for what is counted."""
import json
import logging
import os
from collections import defaultdict
from collections.abc import Iterable, Mapping
from pathlib import Path

from integrations.weekly_sources import TEAM_ALIASES

log = logging.getLogger(__name__)

SEGMENTS_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "script_segments.json"
CUT = 600                        # seconds of regulation in the closing window (sim_closing_fit.WINDOW)
COUNTS = ("margin", "targets", "attempts", "sacks", "carries", "qb_carries", "qb_yards", "kneels", "kneel_yards",
          "scrambles", "spikes")


def _download(season: int) -> list[dict]:
    """One season's regular-season games with each team's counts per part (see the module docstring)."""
    import nflreadpy as nfl
    import polars as pl

    cols = ["game_id", "week", "home_team", "away_team", "spread_line", "qtr", "game_seconds_remaining", "play_id",
            "posteam", "defteam", "posteam_score", "defteam_score", "play_type", "pass_attempt", "rush_attempt", "sack",
            "receiver_player_id", "two_point_attempt", "qb_kneel", "qb_spike", "qb_scramble", "rusher_player_id",
            "passer_player_id", "yards_gained"]
    pbp = nfl.load_pbp(season).filter(pl.col("season_type") == "REG").select(cols).sort(["game_id", "play_id"])
    return [g for _, rows in pbp.group_by("game_id", maintain_order=True)
            if (g := game_counts(list(rows.iter_rows(named=True)), season)) is not None]


def _passers(rows: list[Mapping]) -> dict[str, str | None]:
    """{team: the id of the player who threw the most passes} (his carries are the quarterback's)."""
    thrown: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["passer_player_id"] is not None and r["posteam"]:
            thrown[r["posteam"]][r["passer_player_id"]] += 1
    return {team: max(counts, key=counts.get) for team, counts in thrown.items()}


def game_counts(rows: list[Mapping], season: int) -> dict | None:
    """One game's {"season", "week", "home", "away", "spread", home team: [part counts x3], away team: ...} from its
    play-by-play rows in play order; None when a part has no play with a score (a broken feed) or no spread."""
    first = rows[0]
    home, away, spread = first["home_team"], first["away_team"], first["spread_line"]
    if spread is None:
        return None
    qb = _passers(rows)
    counts = {(t, part): dict.fromkeys(COUNTS, 0) for t in (home, away) for part in range(3)}
    entering: dict[int, dict[str, int]] = {}
    for r in rows:
        qtr, left, team = r["qtr"], r["game_seconds_remaining"], r["posteam"]
        if qtr is None or qtr > 4 or left is None or team not in (home, away):
            continue
        part = 0 if qtr <= 2 else 1 if left > CUT else 2
        if part not in entering and r["posteam_score"] is not None:
            entering[part] = {team: r["posteam_score"], r["defteam"]: r["defteam_score"]}
        if r["two_point_attempt"] == 1 or r["play_type"] in (None, "no_play"):
            continue
        c = counts[(team, part)]
        if r["rush_attempt"] == 1:
            c["carries"] += 1
            mine = r["qb_kneel"] == 1 or (r["rusher_player_id"] is not None and r["rusher_player_id"] == qb.get(team))
            c["qb_carries"] += int(mine)
            c["qb_yards"] += int(r["yards_gained"] or 0) if mine else 0
            c["kneels"] += int(r["qb_kneel"] == 1)
            c["kneel_yards"] += int(r["yards_gained"] or 0) if r["qb_kneel"] == 1 else 0
            c["scrambles"] += int(r["qb_scramble"] == 1)
        if r["pass_attempt"] == 1:
            if r["sack"] == 1:
                c["sacks"] += 1
            else:
                c["attempts"] += 1
                c["spikes"] += int(r["qb_spike"] == 1)
                c["targets"] += int(r["receiver_player_id"] is not None)
    if len(entering) < 3:
        return None
    out = {"season": season, "week": first["week"], "home": TEAM_ALIASES.get(home, home),
           "away": TEAM_ALIASES.get(away, away), "spread": float(spread)}
    for team, other in ((home, away), (away, home)):
        parts = []
        for part in range(3):
            c = {**counts[(team, part)], "margin": entering[part].get(team, 0) - entering[part].get(other, 0)}
            parts.append([c[k] for k in COUNTS])
        out[TEAM_ALIASES.get(team, team)] = parts
    return out


def segments(seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """Every real game of `seasons` with its parts' counts, from SEGMENTS_CACHE (downloaded when missing, written in
    another column layout, or with `refresh`; the other seasons in the cache stay)."""
    wanted = sorted(set(seasons))
    cached = _cached()
    missing = [s for s in wanted if refresh or str(s) not in cached]
    if missing:
        for season in missing:
            log.info("downloading %s play-by-play", season)
            cached[str(season)] = _download(season)
        SEGMENTS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(SEGMENTS_CACHE, json.dumps(cached))
    return [g for s in wanted for g in cached[str(s)]]


def atomic_write(path: Path, text: str) -> None:
    """`text` to `path` through a temporary file, which a failed write never leaves behind."""
    partial = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        partial.write_text(text, encoding="utf-8")
        os.replace(partial, path)
    finally:
        partial.unlink(missing_ok=True)


def _cached() -> dict:
    """SEGMENTS_CACHE's seasons whose every game has len(COUNTS) counts per part (a season cached in another
    column layout is left out, so it is downloaded again: reading it by position would mix the columns up)."""
    try:
        cached = json.loads(SEGMENTS_CACHE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("%s is unreadable: downloading the play-by-play again", SEGMENTS_CACHE.name, exc_info=True)
        return {}
    if not (isinstance(cached, dict) and all(isinstance(v, list) for v in cached.values())):
        log.warning("%s is not a {season: games} file: downloading the play-by-play again", SEGMENTS_CACHE.name)
        return {}
    kept = {season: games for season, games in cached.items() if all(_laid_out(g) for g in games)}
    if len(kept) < len(cached):
        log.warning("%s: seasons %s were cached in another column layout: downloading them again",
                    SEGMENTS_CACHE.name, sorted(set(cached) - set(kept)))
    return kept


def _laid_out(game: Mapping) -> bool:
    teams = [game.get("home"), game.get("away")]
    return all(isinstance(game.get(t), list) and len(game[t]) == 3 and all(len(part) == len(COUNTS) for part in game[t])
               for t in teams)


def window_counts(game: Mapping, team: str) -> dict[str, int]:
    """A team's closing-window targets, carries and kneels in one cached game (sim_closing_fit's ending table)."""
    part = dict(zip(COUNTS, game[team][2]))
    return {"targets": int(part["targets"]), "carries": int(part["carries"]), "kneels": int(part["kneels"])}
