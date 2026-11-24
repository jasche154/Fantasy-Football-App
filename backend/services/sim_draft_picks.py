"""nflverse draft picks for the simulator's fits (sim_next_year_fit, sim_eval's full mode), cached on disk.

The cache (PICKS_CACHE, git-ignored) holds one nflreadpy download, raw, in nflverse's order, with its date, so
the network is needed only the first time, with `refresh`, or when a wanted season is not settled in it.
nflreadpy is imported only when a download is needed.
"""
import json
import logging
import os
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from integrations.weekly_sources import TEAM_ALIASES

log = logging.getLogger(__name__)

PICKS_CACHE = Path(__file__).resolve().parent.parent / "data" / "cache" / "draft_picks.json"
PICK_FIELDS = ("season", "pick", "team", "gsis_id", "position", "pfr_player_name", "college")
SETTLED_MONTH = 9           # a draft's download is final from September 1 of its year (gsis ids fill in by then)


def draft_picks(seasons: Iterable[int], refresh: bool = False) -> list[dict]:
    """nflverse draft picks: season, pick, team (schedule code), gsis_id, position, name, college. Read from
    PICKS_CACHE; downloaded (nflreadpy) only with `refresh` or when a wanted season is not settled in it (see
    _settled; so a season not drafted yet, or drafted but without its gsis ids yet, is asked for again). The
    file holds one download, raw, in nflverse's order, with its date."""
    wanted = set(seasons)
    cached = {} if refresh else _cached_picks()
    rows = cached.get("rows", [])
    if not wanted <= _settled(cached):
        rows = _download(sorted({r["season"] for r in rows} | wanted))
        PICKS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        partial = PICKS_CACHE.with_suffix(".tmp")            # written whole, then swapped in: never half a file
        partial.write_text(json.dumps({"downloaded": _today().isoformat(), "rows": rows}), encoding="utf-8")
        os.replace(partial, PICKS_CACHE)
    return [{"season": int(r["season"]), "pick": int(r["pick"]), "team": TEAM_ALIASES.get(r["team"], r["team"]),
             "gsis_id": r["gsis_id"], "position": r["position"], "name": r["pfr_player_name"],
             "college": r.get("college") or "-"} for r in rows if r["season"] in wanted and r.get("gsis_id")]


def _settled(cached: dict) -> set[int]:
    """The seasons in the file downloaded on or after September 1 of their year: a new draft's gsis ids fill in
    over the summer, so a draft-week download is not final."""
    if not cached:
        return set()
    downloaded = date.fromisoformat(cached["downloaded"])
    return {r["season"] for r in cached["rows"] if date(r["season"], SETTLED_MONTH, 1) <= downloaded}


def _today() -> date:
    return date.today()


def _download(seasons: list[int]) -> list[dict]:
    import nflreadpy as nfl

    return [{key: r.get(key) for key in PICK_FIELDS} for r in nfl.load_draft_picks(seasons).to_dicts()]


def _cached_picks() -> dict:
    try:
        cached = json.loads(PICKS_CACHE.read_text(encoding="utf-8"))
        date.fromisoformat(cached["downloaded"])
        rows = cached["rows"]
        if isinstance(rows, list) and all(isinstance(r, dict) and isinstance(r.get("season"), int) for r in rows):
            return cached
    except FileNotFoundError:
        return {}                               # none yet
    except (OSError, ValueError, KeyError, TypeError):
        pass
    log.info("%s is unreadable or the wrong shape: downloading the draft picks again", PICKS_CACHE.name)
    return {}
