import nflreadpy as nfl
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from models import (
    Base, Player, PlayerStatsWeekly,
    PlayerPassingAdvancedWeekly, PlayerReceivingAdvancedWeekly, PlayerRushingAdvancedWeekly,
    PlayerExpectedPointsWeekly
)
from db import engine

Base.metadata.create_all(engine)
SEASONS = [2021, 2022, 2023, 2024, 2025, 2026]
CURRENT_SEASONS = [nfl.get_current_season()]  # re-deleted and reloaded every run


def get_or_create_player(session, gsis_id, display_name, position, team):
    statement = select(Player).where(Player.gsis_id == gsis_id)
    player = session.execute(statement).scalar_one_or_none()
    if player is None:
        player = Player(gsis_id=gsis_id, full_name=display_name, position=position, nfl_team=team)
        session.add(player)
        session.flush()
    return player


with Session(engine) as session:

    # --- Normal weekly stats ---
    session.execute(delete(PlayerStatsWeekly).where(PlayerStatsWeekly.season.in_(CURRENT_SEASONS)))
    session.commit()

    player_stats = nfl.load_player_stats(SEASONS)
    for row in player_stats.iter_rows(named=True):
        if row["season"] not in CURRENT_SEASONS:
            continue
        if row["player_id"] is None or row["player_display_name"] is None:
            continue
        player = get_or_create_player(
            session, row["player_id"], row["player_display_name"],
            row["position"], row["team"]
        )
        session.add(PlayerStatsWeekly(
            player_id=player.id,
            season=row["season"],
            week=row["week"],
            season_type=row["season_type"],
            game_id=row["game_id"],
            opponent_team=row["opponent_team"],
            completions=row["completions"],
            attempts=row["attempts"],
            passing_yards=row["passing_yards"],
            passing_tds=row["passing_tds"],
            passing_interceptions=row["passing_interceptions"],
            sacks_suffered=row["sacks_suffered"],
            sack_yards_lost=row["sack_yards_lost"],
            sack_fumbles=row["sack_fumbles"],
            sack_fumbles_lost=row["sack_fumbles_lost"],
            passing_air_yards=row["passing_air_yards"],
            passing_yards_after_catch=row["passing_yards_after_catch"],
            passing_first_downs=row["passing_first_downs"],
            passing_epa=row["passing_epa"],
            passing_cpoe=row["passing_cpoe"],
            passing_2pt_conversions=row["passing_2pt_conversions"],
            pacr=row["pacr"],
            passing_10=row["passing_10"],
            passing_16=row["passing_16"],
            passing_20=row["passing_20"],
            passing_40=row["passing_40"],
            carries=row["carries"],
            rushing_yards=row["rushing_yards"],
            rushing_tds=row["rushing_tds"],
            rushing_fumbles=row["rushing_fumbles"],
            rushing_fumbles_lost=row["rushing_fumbles_lost"],
            rushing_first_downs=row["rushing_first_downs"],
            rushing_epa=row["rushing_epa"],
            rushing_2pt_conversions=row["rushing_2pt_conversions"],
            rushing_10=row["rushing_10"],
            rushing_12=row["rushing_12"],
            rushing_20=row["rushing_20"],
            rushing_40=row["rushing_40"],
            receptions=row["receptions"],
            targets=row["targets"],
            receiving_yards=row["receiving_yards"],
            receiving_tds=row["receiving_tds"],
            receiving_fumbles=row["receiving_fumbles"],
            receiving_fumbles_lost=row["receiving_fumbles_lost"],
            receiving_air_yards=row["receiving_air_yards"],
            receiving_yards_after_catch=row["receiving_yards_after_catch"],
            receiving_first_downs=row["receiving_first_downs"],
            receiving_epa=row["receiving_epa"],
            receiving_2pt_conversions=row["receiving_2pt_conversions"],
            receiving_10=row["receiving_10"],
            receiving_16=row["receiving_16"],
            receiving_20=row["receiving_20"],
            receiving_40=row["receiving_40"],
            racr=row["racr"],
            target_share=row["target_share"],
            air_yards_share=row["air_yards_share"],
            wopr=row["wopr"],
            special_teams_tds=row["special_teams_tds"],
            fantasy_points=row["fantasy_points"],
            fantasy_points_ppr=row["fantasy_points_ppr"],
        ))

    session.commit()
    print("Normal weekly stats done.")

    # --- Passing advanced ---
    session.execute(delete(PlayerPassingAdvancedWeekly).where(PlayerPassingAdvancedWeekly.season.in_(CURRENT_SEASONS)))
    session.commit()

    passing_ngs = nfl.load_nextgen_stats(seasons=SEASONS, stat_type="passing")
    for row in passing_ngs.iter_rows(named=True):
        if row["season"] not in CURRENT_SEASONS:
            continue
        if row["player_gsis_id"] is None:
            continue
        player = get_or_create_player(
            session, row["player_gsis_id"], row["player_display_name"],
            row["player_position"], row["team_abbr"]
        )
        session.add(PlayerPassingAdvancedWeekly(
            player_id=player.id,
            season=row["season"],
            week=row["week"],
            season_type=row["season_type"],
            avg_time_to_throw=row["avg_time_to_throw"],
            avg_completed_air_yards=row["avg_completed_air_yards"],
            avg_intended_air_yards=row["avg_intended_air_yards"],
            avg_air_yards_differential=row["avg_air_yards_differential"],
            aggressiveness=row["aggressiveness"],
            max_completed_air_distance=row["max_completed_air_distance"],
            avg_air_yards_to_sticks=row["avg_air_yards_to_sticks"],
            attempts=row["attempts"],
            pass_yards=row["pass_yards"],
            pass_touchdowns=row["pass_touchdowns"],
            interceptions=row["interceptions"],
            passer_rating=row["passer_rating"],
            completions=row["completions"],
            completion_percentage=row["completion_percentage"],
            expected_completion_percentage=row["expected_completion_percentage"],
            completion_percentage_above_expectation=row["completion_percentage_above_expectation"],
            avg_air_distance=row["avg_air_distance"],
            max_air_distance=row["max_air_distance"],
        ))

    session.commit()
    print("Passing advanced stats done.")

    # --- Receiving advanced ---
    session.execute(delete(PlayerReceivingAdvancedWeekly).where(PlayerReceivingAdvancedWeekly.season.in_(CURRENT_SEASONS)))
    session.commit()

    receiving_ngs = nfl.load_nextgen_stats(seasons=SEASONS, stat_type="receiving")
    for row in receiving_ngs.iter_rows(named=True):
        if row["season"] not in CURRENT_SEASONS:
            continue
        if row["player_gsis_id"] is None:
            continue
        player = get_or_create_player(
            session, row["player_gsis_id"], row["player_display_name"],
            row["player_position"], row["team_abbr"]
        )
        session.add(PlayerReceivingAdvancedWeekly(
            player_id=player.id,
            season=row["season"],
            week=row["week"],
            season_type=row["season_type"],
            avg_cushion=row["avg_cushion"],
            avg_separation=row["avg_separation"],
            avg_intended_air_yards=row["avg_intended_air_yards"],
            percent_share_of_intended_air_yards=row["percent_share_of_intended_air_yards"],
            receptions=row["receptions"],
            targets=row["targets"],
            catch_percentage=row["catch_percentage"],
            yards=row["yards"],
            rec_touchdowns=row["rec_touchdowns"],
            avg_yac=row["avg_yac"],
            avg_expected_yac=row["avg_expected_yac"],
            avg_yac_above_expectation=row["avg_yac_above_expectation"],
        ))

    session.commit()
    print("Receiving advanced stats done.")

    # --- Rushing advanced ---
    session.execute(delete(PlayerRushingAdvancedWeekly).where(PlayerRushingAdvancedWeekly.season.in_(CURRENT_SEASONS)))
    session.commit()

    rushing_ngs = nfl.load_nextgen_stats(seasons=SEASONS, stat_type="rushing")
    for row in rushing_ngs.iter_rows(named=True):
        if row["season"] not in CURRENT_SEASONS:
            continue
        if row["player_gsis_id"] is None:
            continue
        player = get_or_create_player(
            session, row["player_gsis_id"], row["player_display_name"],
            row["player_position"], row["team_abbr"]
        )
        session.add(PlayerRushingAdvancedWeekly(
            player_id=player.id,
            season=row["season"],
            week=row["week"],
            season_type=row["season_type"],
            efficiency=row["efficiency"],
            percent_attempts_gte_eight_defenders=row["percent_attempts_gte_eight_defenders"],
            avg_time_to_los=row["avg_time_to_los"],
            rush_attempts=row["rush_attempts"],
            rush_yards=row["rush_yards"],
            avg_rush_yards=row["avg_rush_yards"],
            rush_touchdowns=row["rush_touchdowns"],
            expected_rush_yards=row["expected_rush_yards"],
            rush_yards_over_expected=row["rush_yards_over_expected"],
            rush_yards_over_expected_per_att=row["rush_yards_over_expected_per_att"],
            rush_pct_over_expected=row["rush_pct_over_expected"],
        ))

    session.commit()
    print("Rushing advanced stats done.")

    # --- Expected Points Weekly ---
    session.execute(delete(PlayerExpectedPointsWeekly).where(PlayerExpectedPointsWeekly.season.in_(CURRENT_SEASONS)))
    session.commit()

    expected_points = nfl.load_ff_opportunity(seasons=SEASONS, stat_type="weekly")
    for row in expected_points.iter_rows(named=True):
        if row["season"] not in CURRENT_SEASONS:
            continue
        if row["player_id"] is None or row["full_name"] is None:
            continue
        player = get_or_create_player(
            session, row["player_id"], row["full_name"],
            row["position"], row["posteam"]
        )
        session.add(PlayerExpectedPointsWeekly(
            player_id=player.id,
            season=row["season"],
            week=row["week"],
            game_id=row["game_id"],
            posteam=row["posteam"],
            # expected
            pass_completions_exp=row["pass_completions_exp"],
            receptions_exp=row["receptions_exp"],
            pass_yards_gained_exp=row["pass_yards_gained_exp"],
            rec_yards_gained_exp=row["rec_yards_gained_exp"],
            rush_yards_gained_exp=row["rush_yards_gained_exp"],
            pass_touchdown_exp=row["pass_touchdown_exp"],
            rec_touchdown_exp=row["rec_touchdown_exp"],
            rush_touchdown_exp=row["rush_touchdown_exp"],
            pass_first_down_exp=row["pass_first_down_exp"],
            rec_first_down_exp=row["rec_first_down_exp"],
            rush_first_down_exp=row["rush_first_down_exp"],
            pass_interception_exp=row["pass_interception_exp"],
            rec_interception_exp=row["rec_interception_exp"],
            pass_fantasy_points_exp=row["pass_fantasy_points_exp"],
            rec_fantasy_points_exp=row["rec_fantasy_points_exp"],
            rush_fantasy_points_exp=row["rush_fantasy_points_exp"],
            total_yards_gained_exp=row["total_yards_gained_exp"],
            total_touchdown_exp=row["total_touchdown_exp"],
            total_first_down_exp=row["total_first_down_exp"],
            total_fantasy_points_exp=row["total_fantasy_points_exp"],
            # diff (actual minus expected, precomputed by the source)
            pass_completions_diff=row["pass_completions_diff"],
            receptions_diff=row["receptions_diff"],
            pass_yards_gained_diff=row["pass_yards_gained_diff"],
            rec_yards_gained_diff=row["rec_yards_gained_diff"],
            rush_yards_gained_diff=row["rush_yards_gained_diff"],
            pass_touchdown_diff=row["pass_touchdown_diff"],
            rec_touchdown_diff=row["rec_touchdown_diff"],
            rush_touchdown_diff=row["rush_touchdown_diff"],
            pass_first_down_diff=row["pass_first_down_diff"],
            rec_first_down_diff=row["rec_first_down_diff"],
            rush_first_down_diff=row["rush_first_down_diff"],
            pass_interception_diff=row["pass_interception_diff"],
            rec_interception_diff=row["rec_interception_diff"],
            pass_fantasy_points_diff=row["pass_fantasy_points_diff"],
            rec_fantasy_points_diff=row["rec_fantasy_points_diff"],
            rush_fantasy_points_diff=row["rush_fantasy_points_diff"],
            total_yards_gained_diff=row["total_yards_gained_diff"],
            total_touchdown_diff=row["total_touchdown_diff"],
            total_first_down_diff=row["total_first_down_diff"],
            total_fantasy_points_diff=row["total_fantasy_points_diff"],
        ))

    session.commit()
    print("Expected points weekly stats done.")