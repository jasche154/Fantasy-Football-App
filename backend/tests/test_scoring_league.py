import pytest

from services.league import LeagueSettings, replacement_levels, starter_counts
from services.scoring import ScoringSettings, expected_points, fantasy_points, scoring_for

# Real nflverse rows with their published fantasy_points / fantasy_points_ppr.
JOSH_ALLEN_2025_W1 = {"passing_yards": 394, "passing_tds": 2, "rushing_yards": 30, "rushing_tds": 2}
DEVONTA_SMITH_2021_W5 = {"receptions": 7, "receiving_yards": 77, "receiving_fumbles_lost": 1,
                         "receiving_2pt_conversions": 1}
ANDRE_ROBERTS_2021_W17 = {"special_teams_tds": 1}


@pytest.mark.parametrize("stats, position, standard, ppr", [
    (JOSH_ALLEN_2025_W1, "QB", 38.76, 38.76),
    (DEVONTA_SMITH_2021_W5, "WR", 7.7, 14.7),
    (ANDRE_ROBERTS_2021_W17, "WR", 6.0, 6.0),
])
def test_fantasy_points_match_nflverse(stats, position, standard, ppr):
    assert fantasy_points(stats, position, scoring_for("std")) == standard
    assert fantasy_points(stats, position, scoring_for("ppr")) == ppr


def test_half_ppr_is_midpoint_and_te_premium_only_applies_to_tight_ends():
    half = scoring_for("half")
    assert fantasy_points(DEVONTA_SMITH_2021_W5, "WR", half) == pytest.approx(11.2)
    premium = scoring_for("ppr", te_premium=0.5)
    assert fantasy_points({"receptions": 4}, "TE", premium) == 6.0
    assert fantasy_points({"receptions": 4}, "WR", premium) == 4.0


def test_missing_stats_count_as_zero():
    assert fantasy_points({"passing_yards": None, "receptions": None}, "QB", ScoringSettings()) == 0.0


def test_unknown_preset_is_rejected():
    with pytest.raises(ValueError, match="Unknown scoring preset"):
        scoring_for("six-point-passing")


def test_expected_points_convert_from_ppr_basis():
    opportunity = {"total_fantasy_points_exp": 15.0, "receptions_exp": 6.0, "pass_touchdown_exp": 0.0}
    assert expected_points(opportunity, "WR", scoring_for("ppr")) == 15.0
    assert expected_points(opportunity, "WR", scoring_for("half")) == 12.0
    assert expected_points(opportunity, "WR", scoring_for("std")) == 9.0
    assert expected_points(opportunity, "TE", scoring_for("ppr", te_premium=0.5)) == 18.0


def test_expected_points_adjust_for_pass_td_value_and_missing_data():
    six_point_passing = ScoringSettings(pass_td=6.0)
    opportunity = {"total_fantasy_points_exp": 20.0, "receptions_exp": 0.0, "pass_touchdown_exp": 1.5}
    assert expected_points(opportunity, "QB", six_point_passing) == 23.0
    assert expected_points({"total_fantasy_points_exp": None}, "QB", six_point_passing) is None


def _pool(counts: dict[str, int], start: float = 30.0) -> list[tuple[str, float]]:
    return [(pos, start - i) for pos, n in counts.items() for i in range(n)]


def test_starter_counts_fill_flex_with_best_remaining():
    pools = {"QB": [20.0] * 5, "RB": [30, 29, 28, 27, 26], "WR": [25, 24, 10, 9], "TE": [12, 11]}
    league = LeagueSettings(teams=2, starters={"QB": 1, "RB": 2, "WR": 1, "TE": 1, "FLEX": 2})
    counts = starter_counts({k: sorted(v, reverse=True) for k, v in pools.items()}, league)
    # Dedicated slots take QB 2, RB 4, WR 2, TE 2. The 4 FLEX slots then go to the best
    # remaining players: RB 26, WR 10, WR 9, and the last slot stays empty (pool exhausted).
    assert counts == {"QB": 2, "RB": 5, "WR": 4, "TE": 2}


def test_superflex_adds_a_qb_eligible_slot_per_team():
    pools = {"QB": [40.0, 39.0, 38.0], "RB": [10.0] * 10, "WR": [10.0] * 10, "TE": [10.0] * 10}
    one_qb = starter_counts(pools, LeagueSettings(teams=2))
    superflex = starter_counts(pools, LeagueSettings(teams=2, superflex=True))
    assert superflex["QB"] == one_qb["QB"] + 1


def test_replacement_level_is_first_player_beyond_rostered_pool():
    league = LeagueSettings(teams=1, bench=0, starters={"QB": 1, "RB": 1, "WR": 1, "TE": 1})
    levels = replacement_levels(_pool({"QB": 3, "RB": 3, "WR": 3, "TE": 3}), league)
    assert levels == {"QB": 29.0, "RB": 29.0, "WR": 29.0, "TE": 29.0}


def test_replacement_level_is_zero_when_pool_is_too_small():
    levels = replacement_levels([("RB", 10.0)], LeagueSettings(teams=12))
    assert levels["RB"] == 0.0 and levels["QB"] == 0.0


def test_league_settings_are_hashable_for_caching():
    assert hash(LeagueSettings()) == hash(LeagueSettings())
    assert LeagueSettings(scoring="half") != LeagueSettings()
