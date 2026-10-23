import pytest

import tools.rushleaders as rushleaders
from services.leaderboards import CATEGORIES, catalog, get_metric, leaderboard
from services.league import LeagueSettings
from services.players import (
    AmbiguousPlayer, PlayerNotFound, player_profile, resolve_player, search_players,
)
from tests.conftest import CURRENT


def test_season_total_row_wins_over_weekly_rows(session, seeded):
    rows = {r.player_id: r for r in leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=0)}
    official = rows[seeded["rb1"]]
    # NGS's own per-attempt figure and attempt count from the week-0 row, not 60/60 from weeks 1-6.
    assert (official.value, official.volume) == (0.95, 70)


def test_weekly_fallback_is_volume_weighted(session, seeded):
    rows = {r.player_id: r for r in leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=0)}
    weekly = rows[seeded["rb2"]]
    # 3 weeks x 2.0 RYOE on 5 att + 3 weeks x 3.0 on 15 att = 15 / 60, not the mean of weekly rates.
    assert weekly.volume == 60 and weekly.value == pytest.approx(0.25)
    pct = {r.player_id: r for r in leaderboard(session, "rushing", "rush_pct_over_expected", CURRENT, min_volume=0)}
    assert pct[seeded["rb2"]].value == pytest.approx(40.0)   # 0-100 scale


def test_min_volume_sorting_limit_and_position(session, seeded):
    assert [r.player_id for r in leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=65)] == [seeded["rb1"]]
    ascending = leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=0, ascending=True)
    assert ascending[0].player_id == seeded["rb2"] and ascending[0].rank == 1
    assert len(leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=0, limit=1)) == 1
    assert leaderboard(session, "rushing", "ryoe_per_att", CURRENT, min_volume=0, position="WR") == []
    assert leaderboard(session, "rushing", "ryoe_per_att", 1999) == []


def test_stats_based_metrics_and_receiving(session, seeded):
    ppg = leaderboard(session, "usage", "ppr_points_per_game", CURRENT, limit=3)
    # Two players tie at 24.0; ties break alphabetically.
    assert [(r.name, r.value, r.volume) for r in ppg[:2]] == [("QB Player 01", 24.0, 6), ("Sell High Guy", 24.0, 6)]
    separation = leaderboard(session, "receiving", "avg_separation", CURRENT, min_volume=0)
    assert separation[0].player_id == seeded["chase"] and separation[0].value == pytest.approx(3.35)


def test_every_catalog_metric_runs(session):
    for category in CATEGORIES:
        for metric in category.metrics:
            assert isinstance(leaderboard(session, category.key, metric.key, CURRENT), list)


def test_metric_validation_and_catalog_shape():
    with pytest.raises(ValueError, match="Unknown category"):
        get_metric("kicking", "fg_pct")
    with pytest.raises(ValueError, match="Unknown metric"):
        get_metric("rushing", "nope")
    entry = catalog()[0]["metrics"][0]
    assert set(entry) == {"key", "label", "description", "volume_label", "default_min_volume", "format",
                          "higher_is_better"}


def test_leaderboard_rejects_unknown_position(session):
    with pytest.raises(ValueError, match="Unknown position"):
        leaderboard(session, "rushing", "ryoe_per_att", CURRENT, position="K")


def test_search_ranks_exact_then_prefix_then_substring(session, seeded):
    names = [r.name for r in search_players(session, "WR Player 0", limit=3)]
    assert names == ["WR Player 01", "WR Player 02", "WR Player 03"]
    assert search_players(session, "ja'marr chase")[0].id == seeded["chase"]
    assert search_players(session, "Player 01", position="TE")[0].name == "TE Player 01"
    assert search_players(session, "   ") == []


def test_search_prefers_active_players_over_retired_prefix_matches(session, seeded):
    from database.models import Player, PlayerStatsWeekly

    retired = Player(full_name="Chase Oldtimer", position="RB", gsis_id="00-retired-rb")
    session.add(retired)
    session.flush()
    session.add(PlayerStatsWeekly(player_id=retired.id, season=2021, week=1, season_type="REG"))
    session.commit()
    names = [r.name for r in search_players(session, "chase")]
    assert names.index("Ja'Marr Chase") < names.index("Chase Oldtimer")


def test_search_falls_back_to_fuzzy_matching(session, seeded):
    assert search_players(session, "Jamarr Chase")[0].id == seeded["chase"]


def test_resolve_player_by_id_name_and_ambiguity(session, seeded):
    assert resolve_player(session, seeded["chase"]).name == "Ja'Marr Chase"
    assert resolve_player(session, str(seeded["chase"])).id == seeded["chase"]
    assert resolve_player(session, "buy low guy").id == seeded["buy"]
    with pytest.raises(AmbiguousPlayer) as ambiguous:
        resolve_player(session, "Mike Williams")
    assert {c.team for c in ambiguous.value.candidates} == {"NYJ", "LAC"}
    with pytest.raises(PlayerNotFound):
        resolve_player(session, 999_999)
    with pytest.raises(PlayerNotFound):
        resolve_player(session, "Zzyzx Qwerty")


def test_player_profile_shape(session, seeded):
    profile = player_profile(session, seeded["buy"], LeagueSettings())
    assert profile["player"]["name"] == "Buy Low Guy" and profile["season"] == CURRENT
    summary = profile["summary"]
    assert summary["games"] == 6 and summary["ppg"] == 12.0 and summary["expected_ppg"] == 20.0
    assert summary["fpoe_per_game"] == -8.0 and summary["snap_pct"] == 77.5 and summary["target_share"] == 0.2
    assert profile["projection"]["trade_value"] > 0
    assert len(profile["game_log"]) == 6 and profile["game_log"][0]["week"] == 1
    assert profile["advanced"] == {"rushing": None, "receiving": None, "passing": None}


def test_profile_advanced_uses_season_total_row(session, seeded):
    profile = player_profile(session, seeded["rb1"], LeagueSettings())
    assert profile["advanced"]["rushing"]["rush_attempts"] == 70


def test_profile_unknown_player(session):
    with pytest.raises(PlayerNotFound):
        player_profile(session, 999_999, LeagueSettings())


@pytest.fixture
def rushing(engine, seeded, monkeypatch):
    monkeypatch.setattr(rushleaders, "engine", engine)
    return rushleaders.get_rushing_leaders


def test_rushing_leaders_player_lookup(rushing):
    exact = rushing("rush_yards_over_expected", CURRENT, min_attempts=0, player_name="rb player 02")
    assert exact == {"rank": 2, "player": "RB Player 02", "rush_yards_over_expected": 0.25}
    fuzzy = rushing("rush_yards_over_expected", CURRENT, min_attempts=0, player_name="RB Playr 01")
    assert fuzzy["player"] == "RB Player 01" and "closest match" in fuzzy["note"]
    assert "error" in rushing("rush_yards_over_expected", CURRENT, min_attempts=0, player_name="Nobody")


def test_rushing_leaders_validation(rushing):
    with pytest.raises(ValueError, match="Unknown metric"):
        rushing("bogus", CURRENT)
    with pytest.raises(ValueError, match="limit"):
        rushing("rush_yards_over_expected", CURRENT, limit=0)
