"""Replay League: each past week's injury tags (step 5 of replay_data.prepare_season).

Q, D and O come from the official weekly injury report; IR, PUP, SUS and EXE from the weekly roster status
(nflverse, through nflreadpy). Both are known before that week's games: a player hurt in game W is still
active in week W's roster and on a reserve list from W + 1. `injury_rows` is pure; `download` is the only
network code, and nflreadpy is imported inside it, never at import time (this module is loaded at app start).
"""
import logging
from collections import Counter

from sqlalchemy import delete, func, insert, or_, select
from sqlalchemy.orm import Session

from database.models import NflGame, Player, ReplayInjury
from integrations.adp_history import TWO_WAY
from integrations.weekly_sources import normalize_team
from services.replay_pool import HARD_TAGS, IR_TAGS

log = logging.getLogger(__name__)

POSITIONS = ("QB", "RB", "WR", "TE", "K")
REPORT_TAGS = {"Questionable": "Q", "Doubtful": "D", "Out": "O"}
SUSPENDED = ("R40", "R33", "R30")
NO_TAG = ("R02", "R03", "R06", "R62")       # retired, did not report, left squad, 2020 COVID opt-out: not hurt
NO_CARRY = ("R05", "R27", "R59")            # non-football injury or illness, COVID list: no carried injury text
NOT_INJURY = ("not injury related", "rest", "personal", "coach")   # report text naming no injury: never carried
ACTIVE = ("ACT", "INA")
NOT_HURT = ("SUS", "EXE")                   # roster moves that are no injury: no practice status or injury text
MAX_TEXT = 60
REPORT_COLUMNS = ["season", "week", "game_type", "gsis_id", "team", "report_status", "practice_status",
                  "report_primary_injury", "practice_primary_injury"]
ROSTER_COLUMNS = ["season", "week", "game_type", "gsis_id", "team", "status", "status_description_abbr"]
# A copy of populate_stats.practice_code: importing populate_stats would load nflreadpy, polars and the engine.
_PRACTICE = {"did not participate": "DNP", "limited": "LP", "full": "FP"}


def _practice(text: str | None) -> str | None:
    lowered = (text or "").lower()
    return next((code for key, code in _PRACTICE.items() if key in lowered), None)


def roster_tag(status: str | None, abbr: str | None) -> str | None:
    """The tag a weekly roster status gives (None for active, cut, holdouts and the reserve lists in NO_TAG:
    retired, did not report, left squad, 2020 opt-out). EXE: the commissioner's exempt list (Josh Gordon 2020)."""
    if status == "EXE":
        return "EXE"
    if status == "PUP" or (status == "RES" and abbr == "R04"):
        return "PUP"
    if status == "SUS" or (status == "RES" and abbr in SUSPENDED):
        return "SUS"
    if status in ("RES", "RSN") and abbr not in NO_TAG:
        return "IR"
    return None


def _by_player_week(rows: list[dict], player_ids: dict[str, int], last_week: int) -> dict[tuple[int, int], list]:
    """D1: regular-season rows of known players in weeks 1..last_week, in file order."""
    grouped: dict[tuple[int, int], list[dict]] = {}
    for row in rows:
        if row.get("game_type") != "REG" or row.get("week") is None or row.get("gsis_id") not in player_ids:
            continue
        week = int(float(row["week"]))
        if 1 <= week <= last_week:
            grouped.setdefault((player_ids[row["gsis_id"]], week), []).append(row)
    return grouped


def _roster(rows: list[dict]) -> tuple[str | None, dict]:
    """D3: (tag, the row it came from); no tag when any row says active or inactive."""
    if any(row.get("status") in ACTIVE for row in rows):
        return None, rows[0]
    return next(((tag, row) for row in rows
                 if (tag := roster_tag(row.get("status"), row.get("status_description_abbr")))), (None, rows[0]))


def _text(report: dict | None) -> str | None:
    if not report:
        return None
    text = (report.get("report_primary_injury") or report.get("practice_primary_injury") or "").strip()
    return text[:MAX_TEXT] or None


def injury_rows(season: int, reports: list[dict], rosters: list[dict], player_ids: dict[str, int],
                playing: set[tuple[str, int]], last_week: int) -> list[dict]:
    """Rows for replay_injuries, sorted by (week, player_id). Rules D1-D6 of the injury spec."""
    report_rows = _by_player_week(reports, player_ids, last_week)
    roster_rows = _by_player_week(rosters, player_ids, last_week)
    tags: dict[tuple[int, int], dict] = {}
    texts: dict[tuple[int, int], str | None] = {}           # the text a player had that week (D5)
    teams: dict[tuple[int, int], str | None] = {}           # his team that week (D6)
    for week in range(1, last_week + 1):
        for pid in sorted({pid for pid, w in (*report_rows, *roster_rows) if w == week}):
            key = (pid, week)
            report = (report_rows.get(key) or [None])[0]                          # D2: the first row wins
            # A new club's report after a trade (Ertz 2021 week 6) says nothing about the game he already played.
            if report and key in roster_rows and normalize_team(report.get("team")) not in {
                    normalize_team(row.get("team")) for row in roster_rows[key]}:
                report = None
            roster_tag_, roster = _roster(roster_rows[key]) if key in roster_rows else (None, None)
            tag = roster_tag_ or REPORT_TAGS.get((report or {}).get("report_status"))      # D4
            teams[key] = normalize_team((roster or {}).get("team") or (report or {}).get("team"))
            own = _text(report)
            texts[key] = None if own and own.lower().startswith(NOT_INJURY) else own    # what next week may carry
            if tag is None:
                continue
            practice = injury = None
            if tag not in NOT_HURT:                                               # D5
                practice = _practice((report or {}).get("practice_status"))
                injury = own
                if injury is None and tag in IR_TAGS and (roster or {}).get("status_description_abbr") not in NO_CARRY:
                    injury = texts[key] = texts.get((pid, week - 1))
            tags[key] = {"season": season, "week": week, "player_id": pid, "tag": tag,
                         "practice": practice, "injury": injury}
        for (pid, prior), row in list(tags.items()):                              # D6: over a bye
            key, team = (pid, week), teams.get((pid, prior))
            if (prior == week - 1 and row["tag"] in HARD_TAGS and key not in report_rows and key not in roster_rows
                    and team is not None and (team, week) not in playing):
                tags[key] = {**row, "week": week, "practice": None}
                texts[key], teams[key] = texts.get((pid, prior)), team
    return sorted(tags.values(), key=lambda row: (row["week"], row["player_id"]))


def download(season: int) -> tuple[list[dict], list[dict]]:
    """(injury report rows, weekly roster rows) for a season from nflverse."""
    import nflreadpy as nfl

    # Columns are picked before any row is read: older files carry timestamps Windows can't decode.
    reports = nfl.load_injuries([season]).select(REPORT_COLUMNS).to_dicts()
    rosters = nfl.load_rosters_weekly([season]).select(ROSTER_COLUMNS).to_dicts()
    return reports, rosters


def load_injuries(session: Session, season: int, force: bool = False) -> dict:
    """Step 5. Store the season's tags, replacing what is stored; a failed download keeps it."""
    stored = session.scalar(select(func.count()).select_from(ReplayInjury).where(ReplayInjury.season == season)) or 0
    if stored and not force:
        return {"rows": stored, "skipped": True}
    try:
        reports, rosters = download(season)
    except Exception as exc:                            # nflreadpy raises a mix of HTTP and parsing errors
        log.warning("injuries %s: download failed (%s)", season, type(exc).__name__)
        return {"rows": stored, "errors": {"injuries": type(exc).__name__}}
    player_ids = dict(session.execute(select(Player.gsis_id, Player.id).where(
        Player.gsis_id.is_not(None), or_(Player.position.in_(POSITIONS), Player.gsis_id.in_(list(TWO_WAY))))).all())
    games = session.execute(select(NflGame.week, NflGame.home_team, NflGame.away_team).where(
        NflGame.season == season, NflGame.game_type == "REG")).all()
    playing = {(team, week) for week, home, away in games for team in (home, away)}
    rows = injury_rows(season, reports, rosters, player_ids, playing, max((g[0] for g in games), default=0))
    session.execute(delete(ReplayInjury).where(ReplayInjury.season == season))
    if rows:
        session.execute(insert(ReplayInjury), rows)
    session.commit()
    return {"rows": len(rows), "weeks": len({row["week"] for row in rows}),
            "tags": dict(Counter(row["tag"] for row in rows))}
