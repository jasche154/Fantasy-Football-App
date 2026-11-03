"""Position percentiles and "what changed" on the player page."""
import pytest

from services.player_percentiles import (
    MIN_CARRIES, POOL_MIN_GAMES, percentile, player_percentiles, pool_min_games,
)
from services.player_usage import RECENT_GAMES, SNAP_SHARE_FLAG, STAT_COLUMNS, UsageGame, what_changed


def game(week: int, snap: float | None = 70.0, target_share: float | None = 20.0, air: float | None = 25.0,
         points: float = 10.0, **stats) -> UsageGame:
    return UsageGame(week=week, stats={c: float(stats.get(c, 0)) for c in STAT_COLUMNS}, fantasy_points=points,
                     target_share=target_share, air_yards_share=air, snap_share=snap)


def by_key(result: dict) -> dict:
    return {row["key"]: row for row in result["stats"]}


def test_percentile_counts_ties_as_half():
    assert percentile(5, [1, 2, 3, 4, 5]) == 90
    assert percentile(1, [1, 2, 3, 4, 5]) == 10
    assert percentile(3, [3, 3]) == 50


def test_pool_min_games_relaxes_early_in_the_season():
    assert pool_min_games({1: [game(w) for w in range(1, 11)]}) == POOL_MIN_GAMES
    assert pool_min_games({1: [game(1), game(2)]}) == 1
    assert pool_min_games({}) == 1


def test_lower_is_better_flips_and_context_has_no_direction():
    qbs = {pid: [game(w, attempts=35, passing_yards=250, passing_interceptions=pid - 1, passing_air_yards=200 + pid)
                 for w in range(1, 7)] for pid in range(1, 6)}
    rows = by_key(player_percentiles(1, "QB", qbs))
    assert rows["int_rate"]["direction"] == "lower" and rows["int_rate"]["percentile"] == 90   # fewest picks
    assert rows["pass_adot"]["direction"] == "context" and rows["pass_adot"]["percentile"] == 10
    assert "targets_pg" not in rows                                  # QB rows only
    worst = by_key(player_percentiles(5, "QB", qbs))
    assert worst["int_rate"]["percentile"] == 10


def test_small_sample_is_excluded_from_the_pool_and_flagged():
    rbs = {pid: [game(w, carries=15, rushing_yards=60 + pid) for w in range(1, 7)] for pid in range(1, 5)}
    rbs[9] = [game(w, carries=2, rushing_yards=40) for w in range(1, 7)]     # 12 carries at 20 ypc
    assert 12 < MIN_CARRIES
    small = by_key(player_percentiles(9, "RB", rbs))["ypc"]
    assert small["small_sample"] and small["percentile"] is None and small["value"] == 20.0
    top = by_key(player_percentiles(4, "RB", rbs))["ypc"]
    assert top["pool_size"] == 4 and top["percentile"] == 88 and not top["small_sample"]


def test_player_below_the_games_threshold_has_no_percentiles():
    pool = {pid: [game(w, targets=8) for w in range(1, 9)] for pid in range(1, 5)}
    pool[7] = [game(1, targets=12)]
    rows = player_percentiles(7, "WR", pool)["stats"]
    assert rows and all(r["percentile"] is None and r["small_sample"] for r in rows)


def test_what_changed_flags_big_moves():
    games = [game(w, snap=60.0, targets=5, carries=1) for w in range(1, 5)]
    games += [game(w, snap=60.0 + SNAP_SHARE_FLAG + 5, targets=6, carries=1) for w in range(5, 5 + RECENT_GAMES)]
    result = what_changed(games, "WR")
    metrics = {m["key"]: m for m in result["metrics"]}
    assert result["recent_weeks"] == [5, 6, 7] and result["baseline_games"] == 4
    assert metrics["snap_share"] == {"key": "snap_share", "label": "Snap share", "unit": "%", "recent": 75.0,
                                     "before": 60.0, "delta": 15.0, "flag": "up", "flag_at": SNAP_SHARE_FLAG}
    assert metrics["targets"]["delta"] == 1.0 and metrics["targets"]["flag"] is None
    assert "carries" not in metrics          # WRs aren't judged on carries


def test_what_changed_needs_a_baseline():
    result = what_changed([game(w) for w in range(1, 4)], "RB")
    assert result["metrics"] == [] and "Needs" in result["note"]


def test_usage_endpoint(client, seeded):
    body = client.get(f"/api/players/{seeded['buy']}/usage").json()
    assert body["season"] == 2026
    rows = by_key(body["percentiles"])
    assert {"ppg", "snap_share", "targets_pg", "target_share", "yards_per_target", "adot"} <= set(rows)
    assert rows["targets_pg"]["percentile"] > 90                    # 7 targets vs the pool's 6
    changes = {m["key"]: m for m in body["changes"]["metrics"]}
    assert changes["snap_share"]["before"] == 70.0 and changes["snap_share"]["recent"] == 85.0
    assert changes["snap_share"]["flag"] == "up"


def test_usage_endpoint_validates(client, seeded):
    assert client.get("/api/players/999999/usage").status_code == 404
    assert client.get(f"/api/players/{seeded['buy']}/usage", params={"season": 2001}).status_code == 404
    assert client.get(f"/api/players/{seeded['buy']}/usage", params={"season": 1900}).status_code == 422
    assert client.get(f"/api/players/{seeded['buy']}/usage", params={"scoring": "bogus"}).status_code == 422


@pytest.mark.parametrize("scoring", ["ppr", "std"])
def test_usage_ppg_follows_scoring(client, seeded, scoring):
    rows = by_key(client.get(f"/api/players/{seeded['buy']}/usage", params={"scoring": scoring}).json()["percentiles"])
    assert rows["ppg"]["value"] == (12.0 if scoring == "ppr" else 7.0)
