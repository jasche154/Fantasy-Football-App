from dataclasses import replace

import pytest
from sqlalchemy import insert

from database.models import PlayerStatsWeekly
from services.league import LeagueSettings
from services.projections import (
    GameLine, PlayerSeason, average_expected, current_rate, latest_season, latest_week,
    load_player_seasons, project_ros_ppg,
)
from services.scoring import scoring_for
from services.valuation import NoDataError, build_valuation, value_players, weeks_remaining
from tests.conftest import CURRENT, PRIOR, stat_row

BLANK_LINE = GameLine(week=1, season_type="REG", opponent=None, fantasy_points=10.0, expected_points=12.0,
                      expected_tds=0.5, passing_yards=0, passing_tds=0, interceptions=0, carries=0,
                      rushing_yards=0, rushing_tds=0, targets=5, receptions=4, receiving_yards=60,
                      receiving_tds=0, target_share=0.2, snap_pct=80.0)


def season_of(points: list[float], expected: list[float | None], position: str = "WR", pid: int = 1) -> PlayerSeason:
    games = tuple(replace(BLANK_LINE, week=i + 1, fantasy_points=p, expected_points=e)
                  for i, (p, e) in enumerate(zip(points, expected)))
    return PlayerSeason(pid, f"Player {pid}", position, "AAA", CURRENT, games)


def test_season_aggregates_and_expected_requires_every_game():
    player = season_of([10, 20, 30], [12, 18, 24])
    assert (player.games_played, player.total_points, player.ppg, player.expected_ppg) == (3, 60, 20.0, 18.0)
    assert season_of([10, 20], [12, None]).expected_ppg is None
    assert average_expected([]) is None
    assert season_of([], []).ppg == 0.0


def test_current_rate_blends_actual_and_expected():
    assert current_rate(season_of([10, 10], [20, 20])) == 14.0   # 0.6 * 10 + 0.4 * 20
    assert current_rate(season_of([10, 10], [None, None])) == 10.0


def test_projection_shrinks_toward_prior_less_as_games_accumulate():
    assert project_ros_ppg(20.0, 0, 10.0) == 10.0
    assert project_ros_ppg(20.0, 4, 10.0) == 15.0
    assert project_ros_ppg(20.0, 12, 10.0) == 17.5


def test_load_player_seasons_uses_regular_season_and_scales_snaps(session, seeded):
    seasons = {p.player_id: p for p in load_player_seasons(session, CURRENT, scoring_for("ppr"))}
    chase = seasons[seeded["chase"]]
    assert chase.games_played == 6 and chase.ppg == 19.0
    assert chase.games[0].snap_pct is None
    buy = seasons[seeded["buy"]]
    assert buy.games[-1].snap_pct == 85.0 and buy.expected_ppg == 20.0
    prior = {p.player_id: p for p in load_player_seasons(session, PRIOR, scoring_for("ppr"))}
    assert seeded["chase"] not in prior       # only a POST game last season


def test_season_and_week_helpers(session):
    assert latest_season(session) == CURRENT
    assert latest_week(session, CURRENT) == 6


def test_weeks_remaining_never_drops_below_one():
    assert weeks_remaining(6, 17) == 11
    assert weeks_remaining(None, 17) == 17
    assert weeks_remaining(18, 17) == 1


def test_value_players_ranks_by_trade_value_with_position_ranks():
    current = [season_of([30] * 4, [30] * 4, "RB", 1), season_of([20] * 4, [20] * 4, "RB", 2),
               season_of([5] * 4, [5] * 4, "RB", 3), season_of([24] * 4, [24] * 4, "WR", 4),
               season_of([18] * 4, [18] * 4, "WR", 5)]
    league = LeagueSettings(teams=1, bench=0, starters={"RB": 1, "WR": 1})
    values, replacement = value_players(current, [], league, remaining=10)
    # RB1 is 10 ppg better than the RB2 baseline; WR1 only 6 better than WR2.
    assert [v.player_id for v in values][:2] == [1, 4]
    assert values[0].pos_rank == "RB1" and values[0].rank == 1 and values[1].pos_rank == "WR1"
    top = values[0]
    assert top.trade_value == pytest.approx(top.vorp_per_game * 10, abs=0.1)
    assert replacement["RB"] > 0
    assert all(v.trade_value >= 0 for v in values)


def test_prior_season_needs_enough_games_to_count():
    current = [season_of([20] * 2, [20] * 2, "WR", 1)]
    league = LeagueSettings(teams=1, bench=0)
    with_prior, _ = value_players(current, [season_of([10] * 8, [10] * 8, "WR", 1)], league, 10)
    short_prior, _ = value_players(current, [season_of([10] * 2, [10] * 2, "WR", 1)], league, 10)
    assert with_prior[0].ros_ppg == project_ros_ppg(20.0, 2, 10.0)
    # A 2-game prior is ignored; the player shrinks toward replacement level (0 in this tiny pool).
    assert short_prior[0].ros_ppg == project_ros_ppg(20.0, 2, 0.0)


def test_build_valuation_on_seeded_league(session, seeded):
    valuation = build_valuation(session, LeagueSettings())
    assert valuation.season == CURRENT and valuation.through_week == 6 and valuation.weeks_remaining == 11
    assert valuation.by_id[seeded["chase"]].trade_value > 0
    assert set(valuation.replacement) == {"QB", "RB", "WR", "TE"}
    assert build_valuation(session, LeagueSettings()) is valuation     # cached


def test_valuation_cache_invalidates_when_stats_reload(session, seeded):
    first = build_valuation(session, LeagueSettings())
    session.execute(insert(PlayerStatsWeekly), [stat_row(seeded["mw1"], CURRENT, 7, "WR", 30.0, 5)])
    session.commit()
    second = build_valuation(session, LeagueSettings())
    assert second is not first and second.through_week == 7


def test_build_valuation_without_data_raises(engine):
    from sqlalchemy.orm import Session

    with Session(engine) as empty, pytest.raises(NoDataError, match="populate_stats"):
        build_valuation(empty, LeagueSettings())


def test_build_valuation_rejects_seasons_without_stats(session):
    with pytest.raises(NoDataError, match="2030"):
        build_valuation(session, LeagueSettings(), season=2030)


def test_same_size_reload_invalidates_cache_via_load_stamp(session, seeded):
    """SQLite reuses rowids, so a delete + reinsert can keep count and max(id) identical."""
    from sqlalchemy import delete, update

    from database.models import DataLoad

    first = build_valuation(session, LeagueSettings())
    session.execute(update(PlayerStatsWeekly).where(PlayerStatsWeekly.player_id == seeded["chase"])
                    .values(receiving_tds=2))
    session.commit()
    assert build_valuation(session, LeagueSettings()) is first        # nothing told the cache yet
    session.add(DataLoad(seasons="2026"))
    session.commit()
    refreshed = build_valuation(session, LeagueSettings())
    assert refreshed is not first and refreshed.by_id[seeded["chase"]].ppg == first.by_id[seeded["chase"]].ppg + 12
    session.execute(delete(DataLoad))
    session.commit()


def test_expected_points_tolerate_a_missing_week():
    one_missing = season_of([10, 20, 30, 40], [12, None, 28, 40])
    assert one_missing.expected_ppg == 26.67
    # FPOE compares the same three games: (10 + 30 + 40) / 3 - 26.67
    assert one_missing.fpoe_per_game == pytest.approx(0.0, abs=0.01)
    assert season_of([10, 20, 30, 40], [12, None, None, 40]).expected_ppg is None   # under 75% coverage
