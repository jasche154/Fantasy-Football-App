"""Team, kicker and red-zone data from nflverse (team stats, player stats, play-by-play).

Loaded by populate_stats for each season it loads:
- team_game_stats: each team's game from the weekly team stats, plus play-by-play counts for
  plays, dropbacks, red-zone trips (drives reaching the opponent 20) and touchdowns, and
  4th-down decisions (go for it vs punt / field goal).
- kicker_games: field goals made and missed by distance, and extra points.
- player_red_zone: each player's carries and targets inside the 20 and the 10.
A season's rows are only replaced after its downloads succeed.
"""
import logging
from collections.abc import Callable, Iterable
from typing import Any

import nflreadpy as nfl
import polars as pl
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from database.models import KickerGame, NflGame, PlayerRedZone, TeamGameStats

log = logging.getLogger("populate_stats")
PLAYS = ["pass", "run"]
DECISIONS = ["pass", "run", "punt", "field_goal"]


def _fetch(fetch: Callable[[list[int]], pl.DataFrame], label: str, season: int) -> pl.DataFrame | None:
    try:
        return fetch([season])
    except Exception as exc:  # nflreadpy raises a mix of HTTP and parsing errors
        log.warning("%s %s: download failed, keeping existing rows (%s)", label, season, type(exc).__name__)
        return None


def _num(value: Any) -> float:
    return float(value) if value is not None else 0.0


def pbp_team_counts(pbp: pl.DataFrame) -> dict[tuple[int, str], dict[str, int]]:
    """{(week, team): plays, dropbacks, red-zone trips/TDs, 4th-down decisions/goes} from play-by-play."""
    reg = pbp.filter(pl.col("season_type") == "REG")
    out: dict[tuple[int, str], dict[str, int]] = {}

    def put(frame: pl.DataFrame, fields: list[str]) -> None:
        for row in frame.iter_rows(named=True):
            out.setdefault((int(row["week"]), row["posteam"]), {}).update({f: int(row[f] or 0) for f in fields})

    plays = reg.filter(pl.col("play_type").is_in(PLAYS) & pl.col("posteam").is_not_null())
    put(plays.group_by(["week", "posteam"]).agg(pl.len().alias("plays"),
                                                pl.col("qb_dropback").fill_null(0).sum().alias("dropbacks")),
        ["plays", "dropbacks"])
    drives = reg.filter((pl.col("drive_inside20") == 1) & pl.col("posteam").is_not_null()).select(
        ["week", "posteam", "fixed_drive", "fixed_drive_result"]).unique()
    put(drives.group_by(["week", "posteam"]).agg(
        pl.len().alias("rz_trips"), (pl.col("fixed_drive_result") == "Touchdown").sum().alias("rz_tds")),
        ["rz_trips", "rz_tds"])
    fourth = reg.filter((pl.col("down") == 4) & pl.col("play_type").is_in(DECISIONS) & pl.col("posteam").is_not_null())
    put(fourth.group_by(["week", "posteam"]).agg(
        pl.len().alias("fourth_downs"), pl.col("play_type").is_in(PLAYS).sum().alias("fourth_down_goes")),
        ["fourth_downs", "fourth_down_goes"])
    return out


def pbp_red_zone(pbp: pl.DataFrame) -> list[dict]:
    """Per player-week: carries and targets inside the 20 and the 10 (gsis ids)."""
    reg = pbp.filter((pl.col("season_type") == "REG") & pl.col("play_type").is_in(PLAYS)
                     & (pl.col("yardline_100") <= 20))
    carries = reg.filter(pl.col("rusher_player_id").is_not_null()).group_by(
        ["week", "posteam", "rusher_player_id"]).agg(
        pl.len().alias("rz_carries"), (pl.col("yardline_100") <= 10).sum().alias("i10_carries")).rename(
        {"rusher_player_id": "gsis"})
    targets = reg.filter(pl.col("receiver_player_id").is_not_null()).group_by(
        ["week", "posteam", "receiver_player_id"]).agg(
        pl.len().alias("rz_targets"), (pl.col("yardline_100") <= 10).sum().alias("i10_targets")).rename(
        {"receiver_player_id": "gsis"})
    merged = carries.join(targets, on=["week", "posteam", "gsis"], how="full", coalesce=True).fill_null(0)
    return list(merged.iter_rows(named=True))


def team_rows(season: int, stats: pl.DataFrame, counts: dict, points: dict) -> list[dict]:
    rows = []
    for s in stats.filter(pl.col("season_type") == "REG").iter_rows(named=True):
        week, team = int(s["week"]), s["team"]
        extra = counts.get((week, team), {})
        scored, allowed = points.get((week, team), (None, None))
        rows.append({
            "season": season, "week": week, "team": team, "opponent": s["opponent_team"],
            "points_for": scored, "points_against": allowed,
            "plays": extra.get("plays", int(_num(s["attempts"]) + _num(s["carries"]) + _num(s["sacks_suffered"]))),
            "dropbacks": extra.get("dropbacks", int(_num(s["attempts"]) + _num(s["sacks_suffered"]))),
            "pass_epa": _num(s["passing_epa"]), "rush_epa": _num(s["rushing_epa"]),
            "sacks_taken": _num(s["sacks_suffered"]), "ints_thrown": int(_num(s["passing_interceptions"])),
            "fumbles_lost": int(_num(s["sack_fumbles_lost"]) + _num(s["rushing_fumbles_lost"])
                                + _num(s["receiving_fumbles_lost"])),
            "def_sacks": _num(s["def_sacks"]), "def_ints": int(_num(s["def_interceptions"])),
            "def_fumble_rec": int(_num(s["fumble_recovery_opp"])),
            "def_tds": int(_num(s["def_tds"]) + _num(s["special_teams_tds"])),
            "def_safeties": int(_num(s["def_safeties"])),
            "def_blocks": int(_num(s["def_punt_blocks"]) + _num(s["def_fg_blocks"]) + _num(s["def_pat_blocks"])),
            "rz_trips": extra.get("rz_trips", 0), "rz_tds": extra.get("rz_tds", 0),
            "fourth_downs": extra.get("fourth_downs", 0), "fourth_down_goes": extra.get("fourth_down_goes", 0),
            "fg_att": int(_num(s["fg_att"])), "fg_made": int(_num(s["fg_made"])),
            "pat_att": int(_num(s["pat_att"])), "pat_made": int(_num(s["pat_made"])),
        })
    return rows


def kicker_rows(season: int, stats: pl.DataFrame, player_ids: dict[str, int]) -> list[dict]:
    rows = []
    kickers = stats.filter((pl.col("position") == "K") & (pl.col("season_type") == "REG"))
    for s in kickers.iter_rows(named=True):
        player_id = player_ids.get(s["player_id"])
        if player_id is None:
            continue
        rows.append({
            "season": season, "week": int(s["week"]), "player_id": player_id, "team": s["team"],
            "opponent": s["opponent_team"],
            "made_0_39": int(_num(s["fg_made_0_19"]) + _num(s["fg_made_20_29"]) + _num(s["fg_made_30_39"])),
            "made_40_49": int(_num(s["fg_made_40_49"])),
            "made_50": int(_num(s["fg_made_50_59"]) + _num(s["fg_made_60_"])),
            "missed_0_39": int(_num(s["fg_missed_0_19"]) + _num(s["fg_missed_20_29"]) + _num(s["fg_missed_30_39"])),
            "missed_40_49": int(_num(s["fg_missed_40_49"])),
            "missed_50": int(_num(s["fg_missed_50_59"]) + _num(s["fg_missed_60_"])),
            "pat_made": int(_num(s["pat_made"])), "pat_att": int(_num(s["pat_att"])),
        })
    return rows


def _points(session: Session, season: int) -> dict[tuple[int, str], tuple[int | None, int | None]]:
    out = {}
    for game in session.scalars(select(NflGame).where(NflGame.season == season, NflGame.game_type == "REG")):
        out[(game.week, game.home_team)] = (game.home_score, game.away_score)
        out[(game.week, game.away_team)] = (game.away_score, game.home_score)
    return out


def load_advanced_stats(session: Session, seasons: Iterable[int], index) -> None:
    """Team game stats, kicker games and red-zone usage for each season (needs schedules loaded first)."""
    for season in seasons:
        pbp = _fetch(lambda s: nfl.load_pbp(s), "play-by-play", season)
        team_stats = _fetch(lambda s: nfl.load_team_stats(s, summary_level="week"), "team stats", season)
        player_stats = _fetch(lambda s: nfl.load_player_stats(s), "kicking stats", season)
        if pbp is None or team_stats is None or player_stats is None:
            continue
        teams = team_rows(season, team_stats, pbp_team_counts(pbp), _points(session, season))
        for row in player_stats.filter(pl.col("position") == "K").select(
                ["player_id", "player_display_name", "position", "team"]).unique().iter_rows(named=True):
            index.resolve(row["player_id"], row["player_display_name"], "K", row["team"])
        kickers = kicker_rows(season, player_stats, index.by_gsis)
        red_zone = []
        for r in pbp_red_zone(pbp):
            player_id = index.by_gsis.get(r["gsis"])
            if player_id is not None:
                red_zone.append({"season": season, "week": int(r["week"]), "player_id": player_id,
                                 "team": r["posteam"], "rz_carries": int(r["rz_carries"]),
                                 "rz_targets": int(r["rz_targets"]), "i10_carries": int(r["i10_carries"]),
                                 "i10_targets": int(r["i10_targets"])})
        for model, rows in ((TeamGameStats, teams), (KickerGame, kickers), (PlayerRedZone, red_zone)):
            session.execute(delete(model).where(model.season == season))
            if rows:
                session.execute(model.__table__.insert(), rows)
        session.commit()
        log.info("advanced %s: %d team games, %d kicker games, %d red-zone rows",
                 season, len(teams), len(kickers), len(red_zone))
