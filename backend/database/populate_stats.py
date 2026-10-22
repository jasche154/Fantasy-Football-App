"""Load nflverse data into the local database.

Run from backend/:
    python -m database.populate_stats                     # load missing seasons + refresh the current one
    python -m database.populate_stats --seasons 2025 2026 # reload specific seasons
    python -m database.populate_stats --all               # reload every season since FIRST_SEASON

Each season is downloaded on its own and only replaces existing rows once the download
succeeds, so a network failure never wipes data that is already loaded.
"""
import argparse
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import nflreadpy as nfl
import polars as pl
from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from database.db import engine
from database.models import (
    Base, ExpertRanking, Player, PlayerExpectedPointsWeekly, PlayerPassingAdvancedWeekly,
    PlayerReceivingAdvancedWeekly, PlayerRushingAdvancedWeekly, PlayerSnapCountsWeekly,
    PlayerStatsWeekly,
)

log = logging.getLogger("populate_stats")

FIRST_SEASON = 2021
BATCH_SIZE = 5000
SKIPPED_COLUMNS = {"id", "player_id"}
EXPERT_PAGES = {"qb": None, "ppr-rb": "ppr", "ppr-wr": "ppr", "ppr-te": "ppr"}


@dataclass(frozen=True)
class Feed:
    """One nflverse dataset keyed by GSIS id, mapped onto one weekly table."""

    name: str
    model: type[Base]
    fetch: Callable[[list[int]], pl.DataFrame]
    id_column: str
    name_column: str
    position_column: str
    team_column: str


FEEDS = [
    Feed("weekly stats", PlayerStatsWeekly, lambda s: nfl.load_player_stats(s),
         "player_id", "player_display_name", "position", "team"),
    Feed("passing NGS", PlayerPassingAdvancedWeekly,
         lambda s: nfl.load_nextgen_stats(seasons=s, stat_type="passing"),
         "player_gsis_id", "player_display_name", "player_position", "team_abbr"),
    Feed("receiving NGS", PlayerReceivingAdvancedWeekly,
         lambda s: nfl.load_nextgen_stats(seasons=s, stat_type="receiving"),
         "player_gsis_id", "player_display_name", "player_position", "team_abbr"),
    Feed("rushing NGS", PlayerRushingAdvancedWeekly,
         lambda s: nfl.load_nextgen_stats(seasons=s, stat_type="rushing"),
         "player_gsis_id", "player_display_name", "player_position", "team_abbr"),
    Feed("expected points", PlayerExpectedPointsWeekly,
         lambda s: nfl.load_ff_opportunity(seasons=s, stat_type="weekly"),
         "player_id", "full_name", "position", "posteam"),
]


def as_int(value: Any) -> int | None:
    return None if value is None else int(float(value))


def as_id(value: Any) -> str | None:
    """Crosswalk ids arrive as ints or strings; store them as plain strings."""
    if value is None:
        return None
    return str(int(value)) if isinstance(value, float) else str(value)


class PlayerIndex:
    """Maps source ids to players.id, creating players the first time they appear."""

    def __init__(self, session: Session):
        self.session = session
        rows = session.execute(select(Player.id, Player.gsis_id, Player.pfr_player_id,
                                      Player.fantasypros_id)).all()
        self.by_gsis = {r.gsis_id: r.id for r in rows if r.gsis_id}
        self.by_pfr = {r.pfr_player_id: r.id for r in rows if r.pfr_player_id}
        self.by_fantasypros = {r.fantasypros_id: r.id for r in rows if r.fantasypros_id}
        self.latest_team: dict[int, tuple[tuple[int, int], str | None, str | None]] = {}

    def resolve(self, gsis_id: str | None, name: str | None, position: str | None,
                team: str | None) -> int | None:
        if not gsis_id:
            return None
        player_id = self.by_gsis.get(gsis_id)
        if player_id is None and name:
            player = Player(gsis_id=gsis_id, full_name=name, position=position, nfl_team=team)
            self.session.add(player)
            self.session.flush()
            player_id = self.by_gsis[gsis_id] = player.id
        return player_id

    def note_team(self, player_id: int, season: int, week: int, team: str | None,
                  position: str | None) -> None:
        current = self.latest_team.get(player_id)
        if current is None or (season, week) >= current[0]:
            self.latest_team[player_id] = ((season, week), team, position)


def data_columns(model: type[Base]) -> list[str]:
    return [c.name for c in model.__table__.columns if c.name not in SKIPPED_COLUMNS]


def bulk_insert(session: Session, model: type[Base], rows: list[dict]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        session.execute(insert(model), rows[start:start + BATCH_SIZE])


def feed_rows(feed: Feed, frame: pl.DataFrame, index: PlayerIndex) -> list[dict]:
    columns = [c for c in data_columns(feed.model) if c in frame.columns]
    rows = []
    for source in frame.iter_rows(named=True):
        player_id = index.resolve(source[feed.id_column], source[feed.name_column],
                                  source[feed.position_column], source[feed.team_column])
        if player_id is None:
            continue
        row = {c: source[c] for c in columns}
        row.update(player_id=player_id, season=as_int(source["season"]), week=as_int(source["week"]))
        if feed.model is PlayerStatsWeekly:
            index.note_team(player_id, row["season"], row["week"], source["team"], source["position"])
        rows.append(row)
    return rows


def fetch_season(fetch: Callable[[list[int]], pl.DataFrame], label: str, season: int) -> pl.DataFrame | None:
    try:
        return fetch([season])
    except Exception as exc:  # nflreadpy raises a mix of HTTP and parsing errors
        log.warning("%s %s: download failed, keeping existing rows (%s)", label, season, exc)
        return None


def load_feed(session: Session, feed: Feed, seasons: Iterable[int], index: PlayerIndex) -> None:
    for season in seasons:
        frame = fetch_season(feed.fetch, feed.name, season)
        if frame is None:
            continue
        rows = feed_rows(feed, frame, index)
        session.execute(delete(feed.model).where(feed.model.season == season))
        bulk_insert(session, feed.model, rows)
        session.commit()
        log.info("%s %s: %d rows", feed.name, season, len(rows))


def apply_latest_teams(session: Session, index: PlayerIndex) -> None:
    players = session.scalars(select(Player).where(Player.id.in_(list(index.latest_team)))).all()
    for player in players:
        _, team, position = index.latest_team[player.id]
        player.nfl_team = team or player.nfl_team
        player.position = position or player.position
    session.commit()


def sync_crosswalk(session: Session, index: PlayerIndex) -> None:
    """Fill platform ids (Yahoo, Sleeper, PFR, FantasyPros) from the DynastyProcess crosswalk."""
    frame = nfl.load_ff_playerids().filter(pl.col("gsis_id").is_not_null())
    frame = frame.sort("db_season", descending=True).unique(subset="gsis_id", keep="first")
    crosswalk = {row["gsis_id"]: row for row in frame.iter_rows(named=True)}
    players = session.scalars(select(Player).where(Player.gsis_id.in_(list(crosswalk)))).all()
    for player in players:
        ids = crosswalk[player.gsis_id]
        player.yahoo_player_id = as_id(ids["yahoo_id"]) or player.yahoo_player_id
        player.sleeper_player_id = as_id(ids["sleeper_id"]) or player.sleeper_player_id
        player.pfr_player_id = as_id(ids["pfr_id"]) or player.pfr_player_id
        player.fantasypros_id = as_id(ids["fantasypros_id"]) or player.fantasypros_id
        if player.pfr_player_id:
            index.by_pfr[player.pfr_player_id] = player.id
        if player.fantasypros_id:
            index.by_fantasypros[player.fantasypros_id] = player.id
    session.commit()
    log.info("crosswalk: updated ids for %d players", len(players))


def load_snap_counts(session: Session, seasons: Iterable[int], index: PlayerIndex) -> None:
    for season in seasons:
        frame = fetch_season(lambda s: nfl.load_snap_counts(seasons=s), "snap counts", season)
        if frame is None:
            continue
        rows, unmatched = [], 0
        for source in frame.iter_rows(named=True):
            player_id = index.by_pfr.get(source["pfr_player_id"])
            if player_id is None:
                unmatched += 1
                continue
            rows.append({
                "player_id": player_id, "season": as_int(source["season"]),
                "week": as_int(source["week"]), "game_type": source["game_type"],
                "team": source["team"], "opponent": source["opponent"],
                "offense_snaps": as_int(source["offense_snaps"]), "offense_pct": source["offense_pct"],
            })
        session.execute(delete(PlayerSnapCountsWeekly).where(PlayerSnapCountsWeekly.season == season))
        bulk_insert(session, PlayerSnapCountsWeekly, rows)
        session.commit()
        log.info("snap counts %s: %d rows (%d without a PFR id match)", season, len(rows), unmatched)


def load_expert_rankings(session: Session, index: PlayerIndex) -> None:
    """Snapshot the current week's FantasyPros ECR as published (free) by DynastyProcess."""
    frame = fetch_season(lambda _: nfl.load_ff_rankings(type="week"), "weekly ECR", nfl.get_current_season())
    if frame is None:
        return
    season, week = nfl.get_current_season(), nfl.get_current_week()
    rows = []
    for source in frame.filter(pl.col("page").is_in(list(EXPERT_PAGES))).iter_rows(named=True):
        fantasypros_id = as_id(source["fantasypros_id"])
        rows.append({
            "player_id": index.by_fantasypros.get(fantasypros_id), "fantasypros_id": fantasypros_id,
            "player_name": source["player_name"], "position": source["pos"], "team": source["team"],
            "source": "nflverse_ffrankings", "ranking_type": "weekly",
            "scoring": EXPERT_PAGES[source["page"]], "season": season, "week": week,
            "rank": as_int(source["rank"]), "ecr": source["ecr"], "pos_rank": source["pos_rank"],
            "sd": source["sd"], "best": as_int(source["best"]), "worst": as_int(source["worst"]),
        })
    session.execute(delete(ExpertRanking).where(ExpertRanking.source == "nflverse_ffrankings"))
    bulk_insert(session, ExpertRanking, rows)
    session.commit()
    log.info("weekly ECR (season %s week %s): %d rows", season, week, len(rows))


def seasons_to_load(session: Session, current_season: int, reload_all: bool) -> list[int]:
    every_season = list(range(FIRST_SEASON, current_season + 1))
    if reload_all:
        return every_season
    loaded = set(session.scalars(select(PlayerStatsWeekly.season).distinct()))
    return [s for s in every_season if s not in loaded or s == current_season]


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--seasons", type=int, nargs="+", help="seasons to (re)load")
    group.add_argument("--all", action="store_true", help=f"reload every season since {FIRST_SEASON}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    args = parse_args(argv)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        seasons = args.seasons or seasons_to_load(session, nfl.get_current_season(), args.all)
        log.info("Loading seasons: %s", seasons)
        index = PlayerIndex(session)
        for feed in FEEDS:
            load_feed(session, feed, seasons, index)
        apply_latest_teams(session, index)
        sync_crosswalk(session, index)
        load_snap_counts(session, seasons, index)
        load_expert_rankings(session, index)
    log.info("Done.")


if __name__ == "__main__":
    main()
