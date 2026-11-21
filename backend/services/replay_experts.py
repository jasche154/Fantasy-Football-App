"""Replay League expert ranks: FantasyPros consensus position ranks from the nflverse archive, point in time.

`download` pulls the archive (nflreadpy, imported there only); `expert_rows` matches it to our players
(pure); `week_ranks` picks, for each week, the latest snapshot taken before that week's first Sunday kickoff
(the preseason cheatsheet: before week 1's first kickoff, its Thursday opener) (pure); `load_expert_ranks` stores rows in `replay_expert_ranks`. Only the last preseason cheatsheet and
every in-season ROS snapshot are kept. A failed download or import is reported, never blocks a season.
See .plans/pr1-plan.md section B.

The archive has PPR position pages only (no overall list, no half or standard), so every scoring format
uses the PPR order. Snapshots are scraped on Fridays, so week W's ranks already know week W-1's games and
any Thursday game of week W (a small, accepted leak).
"""
import logging
import re
from collections import defaultdict
from collections.abc import Mapping
from datetime import date, timedelta

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from database.models import NflGame, ReplayExpertRank
from integrations.adp_history import SeasonMatcher
from services.replay_pool import PlayerWeek

log = logging.getLogger(__name__)

FIRST_SEASON = 2020
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
PAGE_PATTERN = r"(ros-)?(ppr-)?(qb|rb|wr|te|k|dst)(-cheatsheets)?(\.php)?$"   # after stripping "/nfl/rankings/"
PAGE_PREFIX = "/nfl/rankings/"
ROS_LEAD_DAYS = 7               # a ROS page scraped in the days before week 1 still counts for week 1
UNMATCHED_SHOWN = 10
_PAGE = re.compile(PAGE_PATTERN)


def _page(page: str | None) -> tuple[str, str] | None:
    """('pre' | 'ros', position) for one of the six position pages, else None."""
    match = _PAGE.fullmatch((page or "").removeprefix(PAGE_PREFIX))
    if match is None or bool(match.group(1)) == bool(match.group(4)):     # ROS pages are never cheatsheets
        return None
    return ("ros" if match.group(1) else "pre"), match.group(3).upper()


def download() -> list[dict]:
    """The archive's "rp" rows for the six position pages."""
    import nflreadpy as nfl
    import polars as pl

    frame = nfl.load_ff_rankings("all").filter(pl.col("ecr_type") == "rp").select(
        "fp_page", "id", "player", "team", "ecr", "scrape_date")
    return [row for row in frame.to_dicts() if _page(row["fp_page"])]


def fp_crosswalk() -> dict[str, str]:
    """FantasyPros id -> gsis id (DynastyProcess, via nflreadpy)."""
    import nflreadpy as nfl

    frame = nfl.load_ff_playerids().select(["fantasypros_id", "gsis_id"]).drop_nulls()
    return {str(fp): gsis for fp, gsis in frame.iter_rows()}


def _sunday(day: date) -> date:
    """The Sunday on or after `day` (a week's first game is a Thursday, sometimes a Saturday)."""
    return day + timedelta(days=(6 - day.weekday()) % 7)


def _snapshots(season: int, raw: list[dict], week1_kickoff: date,
               last_game: date) -> dict[tuple[str, str, str], list[dict]]:
    """{(kind, position, scrape_date): rows} for the season: the last cheatsheet per position scraped before
    week 1's first kickoff (`week1_kickoff`), and every ROS page from just before week 1 to the last game."""
    pages: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    first_ros = _sunday(week1_kickoff) - timedelta(days=ROS_LEAD_DAYS)
    for row in raw:
        kind_position, scraped = _page(row.get("fp_page")), str(row.get("scrape_date") or "")
        try:
            day = date.fromisoformat(scraped)
        except ValueError:
            continue
        if kind_position is None or row.get("ecr") is None:
            continue
        kind, position = kind_position
        if (kind == "pre" and day.year == season and day < week1_kickoff) or (
                kind == "ros" and first_ros <= day <= last_game):
            pages[(kind, position, scraped)].append(row)
    latest_pre = {}
    for kind, position, scraped in pages:
        if kind == "pre":
            latest_pre[position] = max(latest_pre.get(position, ""), scraped)
    return {key: rows for key, rows in pages.items() if key[0] == "ros" or latest_pre[key[1]] == key[2]}


def _player(row: dict, position: str, matcher: SeasonMatcher, fp_to_gsis: dict[str, str]) -> int | None:
    if position == "DST":
        return matcher.defense(row.get("team"))
    return (matcher.by_gsis.get(fp_to_gsis.get(str(row.get("id") or ""), ""))
            or matcher.by_name(str(row.get("player") or ""), position))


def expert_rows(season: int, raw: list[dict], matcher: SeasonMatcher, fp_to_gsis: dict[str, str],
                week1_kickoff: date, last_game: date) -> list[dict]:
    """Rows for `replay_expert_ranks`, matched by FantasyPros id, then name (matcher.by_name), then team
    defense (matcher.defense). `week1_kickoff` is week 1's first game day. pos_rank counts every row of the
    page, matched or not; a player listed twice in one snapshot keeps his better line."""
    out: list[dict] = []
    unmatched: list[str] = []
    for (kind, position, scraped), rows in sorted(_snapshots(season, raw, week1_kickoff, last_game).items()):
        seen: set[int] = set()
        for pos_rank, row in enumerate(sorted(rows, key=lambda r: r["ecr"]), start=1):
            player_id = _player(row, position, matcher, fp_to_gsis)
            if player_id is None:
                unmatched.append(f"{row.get('player')} ({position})")
            elif player_id not in seen:
                seen.add(player_id)
                out.append({"season": season, "kind": kind, "scrape_date": scraped, "player_id": player_id,
                            "position": position, "pos_rank": pos_rank, "ecr": float(row["ecr"])})
    # A player on two position pages in one snapshot (e.g. a QB/TE) keeps his better line: the table's key has no position.
    best: dict[tuple[str, str, int], dict] = {}
    for row in out:
        key = (row["kind"], row["scrape_date"], row["player_id"])
        if key not in best or row["pos_rank"] < best[key]["pos_rank"]:
            best[key] = row
    out = list(best.values())
    if unmatched:
        names = sorted(set(unmatched))
        log.info("Experts %s: %d unmatched lines (%d names), e.g. %s", season, len(unmatched), len(names),
                 "; ".join(names[:UNMATCHED_SHOWN]))
    return out


def _ranks(pages: Mapping[tuple[str, str], Mapping[str, list[dict]]], kind: str,
           before: date) -> tuple[dict[int, int], date | None]:
    """Per position, the latest `kind` snapshot scraped before `before`: ({player: pos_rank}, newest date)."""
    ranks: dict[int, int] = {}
    newest: date | None = None
    for (page_kind, _), by_date in pages.items():
        dates = [d for d in by_date if page_kind == kind and date.fromisoformat(d) < before]
        if not dates:
            continue
        chosen = max(dates)
        newest = max(newest or date.min, date.fromisoformat(chosen))
        for row in by_date[chosen]:
            ranks.setdefault(row["player_id"], row["pos_rank"])
    return ranks, newest


def week_ranks(rows: list[dict], kickoffs: Mapping[int, date]) -> tuple[dict[PlayerWeek, int], dict[int, int]]:
    """({(player_id, week): position rank}, {week: snapshot age in days}). Week 0 = preseason.

    `kickoffs` maps each week to its first game date. Week 0 (the draft) holds the last cheatsheet scraped
    before week 1's first kickoff, so nothing from the opener leaks in; week W the latest ROS page per
    position scraped before week W's Sunday (its first date moved to that Sunday), and only when there is one.
    The age is counted from that cutoff to the newest page used."""
    pages: dict[tuple[str, str], dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        pages[(row["kind"], row["position"])][row["scrape_date"]].append(row)
    sundays = {week: _sunday(day) for week, day in kickoffs.items()}
    ranks: dict[PlayerWeek, int] = {}
    age: dict[int, int] = {}
    wanted = [(0, "pre", kickoffs.get(1, date.max))] + [(w, "ros", sundays[w]) for w in sorted(sundays) if w >= 1]
    for week, kind, sunday in wanted:
        found, newest = _ranks(pages, kind, sunday)
        if newest is None:
            continue
        ranks.update({(player_id, week): rank for player_id, rank in found.items()})
        if sunday != date.max:
            age[week] = (sunday - newest).days
    return ranks, age


def _season_dates(session: Session, season: int) -> tuple[date, date] | None:
    """(week 1's first game day, the last regular-season game day) from the stored schedule."""
    rows = session.execute(select(NflGame.week, NflGame.gameday).where(
        NflGame.season == season, NflGame.game_type == "REG", NflGame.gameday.is_not(None))).all()
    days = [(week, date.fromisoformat(day)) for week, day in rows]
    week1 = [day for week, day in days if week == 1]
    return (min(week1), max(day for _, day in days)) if week1 else None


def stored_rows(session: Session, season: int) -> int:
    return session.scalar(select(func.count()).select_from(ReplayExpertRank).where(
        ReplayExpertRank.season == season)) or 0


def load_expert_ranks(session: Session, season: int, force: bool = False) -> dict:
    """Downloads and stores a season's ranks; {"rows": n, ...}. Before FIRST_SEASON: skipped. A failure
    leaves stored rows as they are and is reported by exception type only."""
    if season < FIRST_SEASON:
        return {"rows": 0, "skipped": "no expert ranks before 2020"}
    stored = stored_rows(session, season)
    if stored and not force:
        return {"rows": stored, "skipped": True}
    dates = _season_dates(session, season)
    if dates is None:
        return {"rows": stored, "errors": {"experts": "NoSchedule"}}
    try:
        raw, crosswalk = download(), fp_crosswalk()
    except Exception as exc:                            # nflreadpy raises a mix of HTTP and parsing errors
        log.warning("experts %s: download failed (%s)", season, type(exc).__name__)
        return {"rows": stored, "errors": {"experts": type(exc).__name__}}
    try:
        rows = expert_rows(season, raw, SeasonMatcher(session, season), crosswalk, *dates)
        if not rows:
            return {"rows": stored, "errors": {"experts": "NoRows"}}
        session.execute(delete(ReplayExpertRank).where(ReplayExpertRank.season == season))
        session.execute(insert(ReplayExpertRank), rows)
        session.commit()
    except Exception as exc:                            # bad archive rows or a DB error: never block prepare_season
        session.rollback()
        log.warning("experts %s: import failed (%s)", season, type(exc).__name__)
        return {"rows": stored, "errors": {"experts": type(exc).__name__}}
    return {"rows": len(rows), "snapshots": len({(r["kind"], r["scrape_date"]) for r in rows})}
