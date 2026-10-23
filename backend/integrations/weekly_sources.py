"""Shared plumbing for the outside weekly sources (Vegas props, Yahoo experts, ESPN).

Each source module fetches one week, turns it into WeeklySourceProjection rows, and stores
them with `replace_snapshot`, which swaps out that (season, week, source) only after a
successful fetch, so a failed refresh never wipes the last good snapshot.
"""
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from database.models import ExternalApiCall, Player, WeeklySourceProjection
from services.league import POSITIONS

USER_AGENT = "Mozilla/5.0 (fantasy-football-app; personal use)"
TIMEOUT_SECONDS = 10.0
FAILURE_BACKOFF_MINUTES = 30
NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# Other sites' abbreviations -> the nflverse abbreviations stored in this database.
TEAM_ALIASES = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "LVR": "LV", "OAK": "LV", "SD": "LAC",
                "STL": "LA", "GNB": "GB", "KAN": "KC", "NWE": "NE", "NOR": "NO", "SFO": "SF",
                "TAM": "TB"}


class SourceError(RuntimeError):
    """An outside source could not be fetched or parsed; the previous snapshot is kept."""


def utc(value: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; stored times are UTC."""
    return value.replace(tzinfo=timezone.utc) if value is not None and value.tzinfo is None else value


def record_call(session: Session, source: str, ok: bool) -> None:
    """Log one outside call (commits). The log drives back-off and survives restarts."""
    session.add(ExternalApiCall(source=source, ok=ok))
    session.commit()


def last_call(session: Session, source: str) -> tuple[datetime, bool] | None:
    row = session.execute(select(ExternalApiCall.called_at, ExternalApiCall.ok)
                          .where(ExternalApiCall.source == source)
                          .order_by(ExternalApiCall.called_at.desc(), ExternalApiCall.id.desc()).limit(1)).first()
    return (utc(row.called_at), row.ok) if row is not None else None


def in_backoff(session: Session, source: str) -> bool:
    """True while the latest call to `source` failed less than FAILURE_BACKOFF_MINUTES ago."""
    latest = last_call(session, source)
    return (latest is not None and not latest[1]
            and datetime.now(timezone.utc) - latest[0] < timedelta(minutes=FAILURE_BACKOFF_MINUTES))


def normalize_name(name: str) -> str:
    """'Kenneth Walker III' -> 'kenneth walker'; "Ja'Marr" -> 'jamarr'; accents dropped."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]", "", ascii_name.lower().replace("-", " ")).split()
    return " ".join(w for w in words if w not in NAME_SUFFIXES)


def normalize_team(team: str | None) -> str | None:
    if not team:
        return None
    upper = team.strip().upper()
    return TEAM_ALIASES.get(upper, upper)


@dataclass(frozen=True)
class _Candidate:
    player_id: int
    team: str | None
    position: str | None


class PlayerMatcher:
    """Resolves an outside player to players.id: platform id first, then name (+team/position)."""

    def __init__(self, session: Session):
        rows = session.execute(select(Player.id, Player.full_name, Player.position, Player.nfl_team,
                                      Player.yahoo_player_id, Player.espn_player_id)
                               .where(Player.position.in_(POSITIONS))).all()
        self.by_yahoo = {r.yahoo_player_id: r.id for r in rows if r.yahoo_player_id}
        self.by_espn = {r.espn_player_id: r.id for r in rows if r.espn_player_id}
        self.position = {r.id: r.position for r in rows}
        self.team = {r.id: r.nfl_team for r in rows}
        self.by_name: dict[str, list[_Candidate]] = {}
        for r in rows:
            self.by_name.setdefault(normalize_name(r.full_name), []).append(
                _Candidate(r.id, r.nfl_team, r.position))

    def match(self, name: str, team: str | None = None, position: str | None = None,
              yahoo_id: str | None = None, espn_id: str | None = None,
              teams: Iterable[str] = ()) -> int | None:
        """`teams` narrows name matches to any of several teams (e.g. both sides of a game)."""
        if yahoo_id and yahoo_id in self.by_yahoo:
            return self.by_yahoo[yahoo_id]
        if espn_id and espn_id in self.by_espn:
            return self.by_espn[espn_id]
        candidates = self.by_name.get(normalize_name(name), [])
        if position:
            candidates = [c for c in candidates if c.position == position] or candidates
        wanted = {t for t in (normalize_team(team), *map(normalize_team, teams)) if t}
        # Prefer the expected team; a unique name elsewhere is usually a recent trade.
        candidates = [c for c in candidates if c.team in wanted] or candidates
        return candidates[0].player_id if len(candidates) == 1 else None


def source_row(season: int, week: int, source: str, *, player_id: int | None, name: str,
               position: str | None, team: str | None, opponent: str | None = None,
               rank: float | None = None, stats: Mapping[str, float] | None = None,
               scoring: str | None = None, expert: str | None = None) -> dict:
    return {"season": season, "week": week, "source": source, "scoring": scoring, "expert": expert,
            "player_id": player_id, "player_name": name, "position": position,
            "team": normalize_team(team), "opponent": normalize_team(opponent), "rank": rank,
            "stats": json.dumps({k: round(v, 3) for k, v in stats.items()}) if stats else None,
            "pct_rostered": None, "fetched_at": datetime.now(timezone.utc)}


def replace_snapshot(session: Session, season: int, week: int, source: str, rows: list[dict],
                     scoring: str | None = None) -> int:
    """Swap the stored rows for one (season, week, source[, scoring]). Commits."""
    if not rows:
        raise SourceError(f"{source} returned no players for week {week}; kept the previous snapshot")
    query = delete(WeeklySourceProjection).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == source)
    if scoring is not None:
        query = query.where(WeeklySourceProjection.scoring == scoring)
    session.execute(query)
    session.execute(insert(WeeklySourceProjection), rows)
    session.commit()
    return len(rows)


def last_fetched(session: Session, season: int, week: int, source: str) -> datetime | None:
    return utc(session.scalar(select(func.max(WeeklySourceProjection.fetched_at)).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == source)))
