"""Yahoo league scoreboards, week by week: remaining pairings (for the season simulation) and
completed weeks' scores (for all-play records and luck, services/all_play.py).

Pairings never change and a finished week's scores are final, so each week is fetched once per
run of the app; missing weeks are fetched in parallel. A week whose matchups aren't all over yet
isn't cached.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import httpx

from integrations import yahoo

SCHEDULE_WORKERS = 6
FINAL_STATUSES = ("postevent", None)          # None: Yahoo left the status out (treated as final)

_schedule: dict[tuple[str, int], list[tuple[str, str]]] = {}      # (league key, week): pairings
_scores: dict[tuple[str, int], dict[str, float | None]] = {}      # (league key, week): {team: points}
_lock = Lock()


def _board(http: httpx.Client, token, league_key: str, week: int) -> list[dict]:
    return yahoo.parse_scoreboard(yahoo.api_get(http, token, f"league/{league_key}/scoreboard;week={week}"))


def _fetch_missing(cache: dict, league_key: str, weeks: range, fetch) -> None:
    with _lock:
        missing = [w for w in weeks if (league_key, w) not in cache]
    if not missing:
        return
    with ThreadPoolExecutor(max_workers=SCHEDULE_WORKERS) as pool:
        fetched = list(pool.map(fetch, missing))
    with _lock:
        for week, value in fetched:
            if value is not None:
                cache[(league_key, week)] = value


def remaining_games(http: httpx.Client, token, league_key: str, first: int, last: int) -> list[tuple[int, str, str]]:
    """Every remaining (week, team, team) pairing from week `first` to `last`."""
    def fetch(week: int) -> tuple[int, list[tuple[str, str]]]:
        board = _board(http, token, league_key, week)
        return week, [(m["teams"][0]["team_key"], m["teams"][1]["team_key"]) for m in board if len(m["teams"]) == 2]

    weeks = range(first, last + 1)
    _fetch_missing(_schedule, league_key, weeks, fetch)
    with _lock:
        return [(w, a, b) for w in weeks for a, b in _schedule.get((league_key, w), [])]


def week_scores(http: httpx.Client, token, league_key: str, last: int) -> dict[int, dict[str, float | None]]:
    """{week: {team key: points}} for weeks 1..last (the completed weeks)."""
    def fetch(week: int) -> tuple[int, dict[str, float | None] | None]:
        board = _board(http, token, league_key, week)
        scores = {t["team_key"]: t["points"] for m in board for t in m["teams"] if t.get("team_key")}
        final = all(m.get("status") in FINAL_STATUSES for m in board)
        return week, scores if final else None

    weeks = range(1, last + 1)
    _fetch_missing(_scores, league_key, weeks, fetch)
    with _lock:
        return {w: dict(_scores[(league_key, w)]) for w in weeks if (league_key, w) in _scores}
