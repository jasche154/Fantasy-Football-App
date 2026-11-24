"""Replay League: what the database holds for a season (re-exported by services/replay_pool, which builds on it).

`readiness` says whether a game can be played on a season; `data_version` changes whenever any row a season is
built from is written, so replay_pool's cache can never hand a new league data older than the database's.
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from database.models import (
    DataLoad, KickerGame, NflGame, Player, PlayerSnapCountsWeekly, PlayerStatsWeekly, ReplayAdp, ReplayExpertRank,
    ReplayInjury, ReplayProjection, TeamGameStats,
)
from services import replay_live
from services.kdst_rankings import DST_PREFIX
from services.projections import REGULAR_SEASON

MIN_ADP_PLAYERS = 180                       # a 12-team, 15-round draft
MIN_KICKERS = MIN_DEFENSES = 14             # one each for the largest league (ADP or a week-1 projection)
SEASON_TABLES = (PlayerStatsWeekly, NflGame, KickerGame, TeamGameStats, PlayerSnapCountsWeekly, ReplayAdp,
                 ReplayProjection, ReplayInjury, ReplayExpertRank)      # every per-season table replay_pool reads


def data_version(session: Session, season: int) -> tuple:
    """Changes whenever a season's data is written, by any loader: (count, max id) of each table it is built from
    and of players, plus the stats loader's stamp (DataLoad: catches a reload that reuses the same ids), as in
    valuation._data_version. One query."""
    parts = [select(func.max(DataLoad.id)).scalar_subquery(), select(func.count(Player.id)).scalar_subquery(),
             select(func.max(Player.id)).scalar_subquery()]
    for model in SEASON_TABLES:
        parts += [select(func.count(model.id)).where(model.season == season).scalar_subquery(),
                  select(func.max(model.id)).where(model.season == season).scalar_subquery()]
    return tuple(session.execute(select(*parts)).one())


def _count(session: Session, column, *where) -> int:
    return session.scalar(select(func.count(column)).where(*where)) or 0


def _schedule_teams(session: Session, season: int) -> set[str]:
    games = session.execute(select(NflGame.home_team, NflGame.away_team).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON))
    return {team for game in games for team in game}


def _draftable(session: Session, season: int, position: str) -> int:
    """Players at a position a draft can find: with an ADP or a week-1 projection."""
    drafted = select(ReplayAdp.player_id).where(ReplayAdp.season == season)
    projected = select(ReplayProjection.player_id).where(
        ReplayProjection.season == season, ReplayProjection.week == 1, ReplayProjection.pts_ppr > 0)
    return _count(session, Player.id, Player.position == position,
                  or_(Player.id.in_(drafted), Player.id.in_(projected)))


def readiness(session: Session, season: int, through: int | None = None) -> dict:
    """What is loaded for a season, and whether a game can be played on it. `through`: a season in progress
    needs projections for weeks 1..through only."""
    teams = _schedule_teams(session, season)
    defenses = set(session.scalars(select(Player.gsis_id).where(Player.position == "DST")))
    has_stats = _count(session, PlayerStatsWeekly.id, PlayerStatsWeekly.season == season,
                       PlayerStatsWeekly.season_type == REGULAR_SEASON) > 0
    has_kdst = (_count(session, KickerGame.id, KickerGame.season == season) > 0
                and _count(session, TeamGameStats.id, TeamGameStats.season == season) > 0
                and all(f"{DST_PREFIX}{team}" in defenses for team in teams))     # every team's D/ST row exists
    adp_players = _count(session, ReplayAdp.player_id.distinct(), ReplayAdp.season == season)   # any format
    weeks = set(session.scalars(select(ReplayProjection.week).distinct().where(ReplayProjection.season == season)))
    last_week = session.scalar(select(func.max(NflGame.week)).where(
        NflGame.season == season, NflGame.game_type == REGULAR_SEASON)) or 0
    every_week = last_week > 0 and set(range(1, (through or last_week) + 1)) <= weeks   # a hole would zero a week
    kickers, defenses = _draftable(session, season, "K"), _draftable(session, season, "DST")
    ready = (has_stats and bool(teams) and has_kdst and adp_players >= MIN_ADP_PLAYERS
             and every_week and kickers >= MIN_KICKERS and defenses >= MIN_DEFENSES)
    projection_weeks = len(weeks)
    injury_weeks = _count(session, ReplayInjury.week.distinct(), ReplayInjury.season == season)  # not needed to play
    expert_snapshots = _count(session, ReplayExpertRank.scrape_date.distinct(), ReplayExpertRank.season == season)
    return {"season": season, "ready": ready, "has_stats": has_stats, "has_schedule": bool(teams),
            "has_kdst": has_kdst, "adp_players": adp_players, "projection_weeks": projection_weeks,
            "kickers": kickers, "defenses": defenses, "injury_weeks": injury_weeks,
            "expert_snapshots": expert_snapshots, "last_week": last_week}


def ready_last_weeks(session: Session) -> dict[int, int]:
    """{season: last week} for the complete seasons (replay_live.replay_seasons) a game can be started on, oldest
    first."""
    rows = (readiness(session, season) for season in replay_live.replay_seasons(session))
    return {row["season"]: row["last_week"] for row in rows if row["ready"]}


def ready_seasons(session: Session, min_last_week: int = 0) -> list[int]:
    """The complete seasons a game can be started on, oldest first; min_last_week: only those that long."""
    return [season for season, last in ready_last_weeks(session).items() if last >= min_last_week]
