"""Vacated-work boosts must not double count what the model's rate already saw, or come from backup QBs."""
import pytest

from services import injuries
from services.injuries import Availability, Boost, InjuryContext
from services.projections import GameLine, PlayerSeason
from services.weekly_adjust import adjust
from services.weekly_model import Matchup, rate_share
from tests.test_injury_model import aaa_schedule

SEASON = 2026


def line(week, points, xp=None):
    return GameLine(week=week, season_type="REG", opponent="OPP", fantasy_points=points, expected_points=xp,
                    expected_tds=None, passing_yards=0, passing_tds=0, interceptions=0, carries=10,
                    rushing_yards=50, rushing_tds=0, targets=3, receptions=2, receiving_yards=20,
                    receiving_tds=0, target_share=None, snap_pct=None)


def season(pid, name, position, games):
    return PlayerSeason(pid, name, position, "AAA", SEASON, tuple(games))


def _matchup():
    return Matchup("OPP", True, sos_factor=1.0, sos_rank=16, implied_total=None, team_ppg=None, game_final=False)


def test_rate_share_is_the_recency_weight_of_those_games_shrunk_by_the_prior():
    player = season(1, "P", "RB", [line(w, 10, xp=10) for w in range(1, 5)])
    assert rate_share(player, {1, 2, 3, 4}, None) == pytest.approx(1.0)
    assert rate_share(player, set(), None) == 0.0
    recent = rate_share(player, {4}, None)
    assert recent > rate_share(player, {1}, None) and recent == pytest.approx(1 / (1 + 2 ** -0.25 + 2 ** -0.5 + 2 ** -0.75))
    assert rate_share(player, {1, 2, 3, 4}, 12.0) == pytest.approx(4 / (4 + 4))   # the prior's part never saw it


def test_boost_knows_how_much_of_the_rate_was_built_without_the_teammate():
    # The starter missed weeks 3-6: the backup's recent games (most of his rate) were without him.
    starter = season(1, "Starter", "RB", [line(w, 18, xp=18) for w in (1, 2)])
    backup = season(2, "Backup", "RB", [line(w, 15 if w > 2 else 6, xp=15 if w > 2 else 6) for w in range(1, 7)])
    avail = {1: Availability(1, 0.0, "Out", None, "Knee", "report")}
    boosts = injuries.vacated_boosts([starter, backup], {1: 18.0, 2: 13.0}, avail, {}, aaa_schedule(), 7)
    (boost,) = boosts[2]
    assert boost.in_rate == pytest.approx(rate_share(backup, {3, 4, 5, 6}, None), abs=0.001)
    assert boost.in_rate > 4 / 6                                         # recent games count more


def test_model_only_projection_adds_only_the_part_of_the_boost_its_rate_lacks():
    boost = Boost(2, 1, "Starter", "Out", full_points=9.0, expected_points=9.0, method="x", in_rate=0.75)
    context = InjuryContext(availability={1: Availability(1, 0.0, "Out", None, None, "report")}, boosts={2: (boost,)})
    alone = adjust("RB", blended=15.0, tilt=1.0, matchup=_matchup(), model_share=1.0, player_id=2, context=context)
    assert alone.boost_points == pytest.approx(9.0 * 0.25)               # not the full 9 on top of a rate that saw it
    blended = adjust("RB", 15.0, 1.0, _matchup(), model_share=0.2, player_id=2, context=context)
    assert blended.boost_points == pytest.approx(9.0 * 0.2 * 0.25)       # outside sources priced the Out tag in


def test_questionable_teammate_keeps_the_outside_share_and_trims_the_models():
    boost = Boost(2, 1, "Starter", "Questionable", full_points=8.0, expected_points=4.0, method="x", in_rate=0.5)
    context = InjuryContext(availability={1: Availability(1, 0.5, "Questionable", "LP", None, "sleeper")},
                            boosts={2: (boost,)})
    result = adjust("RB", 10.0, 1.0, _matchup(), model_share=0.25, player_id=2, context=context)
    assert result.boost_points == pytest.approx(4.0 * (0.75 + 0.25 * 0.5))


def test_a_backup_qb_sitting_gives_the_starter_nothing():
    # The backup only played in blowouts (weeks 2 and 5), when the starter was pulled early.
    starter = season(1, "Starter", "QB", [line(w, 12 if w in (2, 5) else 22, xp=12 if w in (2, 5) else 22)
                                          for w in range(1, 7)])
    backup = season(2, "Backup", "QB", [line(w, 5, xp=5) for w in (2, 5)])
    sits = {2: Availability(2, 0.02, "Backup QB (QB2)", None, None, "depth chart")}
    assert 1 not in injuries.vacated_boosts([starter, backup], {1: 20.0, 2: 5.0}, sits, {}, aaa_schedule(), 7)


def test_a_starting_qb_out_still_moves_work_to_his_backup():
    starter = season(1, "Starter", "QB", [line(w, 22, xp=22) for w in (1, 2, 3, 4)])
    backup = season(2, "Backup", "QB", [line(w, 16 if w > 4 else 2, xp=16 if w > 4 else 2) for w in (2, 3, 5, 6)])
    out = {1: Availability(1, 0.0, "Out", None, "Ankle", "report")}
    boosts = injuries.vacated_boosts([starter, backup], {1: 20.0, 2: 8.0}, out, {}, aaa_schedule(), 7)
    assert boosts[2][0].from_name == "Starter"
