"""Seasons before FIRST_SEASON are loaded only for Replay League: the live app never offers or reads them."""
import pytest
from sqlalchemy import insert, select

from database.models import KickerGame, Player, PlayerStatsWeekly
from services import kdst_model, projections, valuation as valuation_module
from services.league import LeagueSettings
from services.players import AmbiguousPlayer, resolve_player
from services.valuation import build_valuation
from tests.conftest import CURRENT, PRIOR, _add_player, stat_row

OLD, FIRST = projections.FIRST_SEASON - 1, projections.FIRST_SEASON


def _copy_weeks(session, season: int, weeks: range, points: float | None = None) -> None:
    """The seeded current-season players again in `season` (at `points` a game when given)."""
    rows = session.execute(select(PlayerStatsWeekly.player_id, Player.position, PlayerStatsWeekly.fantasy_points_ppr)
                           .join(Player, Player.id == PlayerStatsWeekly.player_id)
                           .where(PlayerStatsWeekly.season == CURRENT, PlayerStatsWeekly.week == 1)).all()
    session.execute(insert(PlayerStatsWeekly), [stat_row(pid, season, week, position, points or ppr)
                                                 for pid, position, ppr in rows for week in weeks])
    session.commit()


def _kicks(session, season: int, week: int, made: int, missed: int, player_id: int) -> None:
    session.add(KickerGame(season=season, week=week, player_id=player_id, team="AAA", opponent="BBB",
                           made_0_39=made, made_40_49=made, made_50=made, missed_0_39=missed,
                           missed_40_49=missed, missed_50=missed, pat_made=3, pat_att=3))
    session.commit()


def test_available_seasons_leave_out_replay_only_seasons(session):
    _copy_weeks(session, OLD, range(1, 3))

    seasons = projections.available_seasons(session)

    assert OLD not in seasons and seasons == [PRIOR, CURRENT]


def test_a_first_season_valuation_ignores_the_replay_only_season_before_it(session):
    _copy_weeks(session, FIRST, range(1, 4))
    league = LeagueSettings()
    without = build_valuation(session, league, FIRST)

    _copy_weeks(session, OLD, range(1, 18), points=3.0)
    valuation_module._cache.clear()
    with_old = build_valuation(session, league, FIRST)

    assert [(v.player_id, v.ros_ppg, v.trade_value) for v in with_old.values] == \
        [(v.player_id, v.ros_ppg, v.trade_value) for v in without.values]
    assert projections.prior_player_seasons(session, FIRST, league.scoring_settings()) == []


def test_first_season_kicker_inputs_ignore_the_replay_only_season(session):
    kicker = _add_player(session, "Kicker One", "K")
    _kicks(session, FIRST, 1, made=1, missed=0, player_id=kicker)
    without = kdst_model.week_inputs(session, FIRST, 2)

    _kicks(session, OLD, 10, made=0, missed=3, player_id=kicker)
    with_old = kdst_model.week_inputs(session, FIRST, 2)

    assert with_old.league_bands == without.league_bands
    assert len(with_old.kicker_rows[kicker]) == 1


def test_a_name_resolves_to_the_active_player_over_a_replay_only_namesake(session):
    active = _add_player(session, "Antonio Williams", "WR", "LAC")
    retired = _add_player(session, "Antonio Williams", "RB", "GB")
    session.execute(insert(PlayerStatsWeekly), [stat_row(active, CURRENT, 1, "WR", 9.0, 3),
                                                stat_row(retired, OLD, 1, "RB", 6.0)])
    session.commit()

    assert resolve_player(session, "Antonio Williams").id == active
    assert resolve_player(session, "antonio williams").id == active


def test_a_namesake_last_seen_in_an_old_live_season_is_still_ambiguous(session):
    recent = _add_player(session, "Jordan Mason", "RB", "MIN")
    hurt = _add_player(session, "Jordan Mason", "WR", "DAL")       # last played in the first live season
    session.execute(insert(PlayerStatsWeekly), [stat_row(recent, CURRENT, 1, "RB", 9.0, 3),
                                                stat_row(hurt, FIRST, 1, "WR", 6.0)])
    session.commit()

    with pytest.raises(AmbiguousPlayer):
        resolve_player(session, "Jordan Mason")


def test_two_active_namesakes_are_still_ambiguous(session):
    with pytest.raises(AmbiguousPlayer):
        resolve_player(session, "Mike Williams")
