"""sim_eval / sim_metrics: the realism gate measures real and simulated seasons the same way (no database)."""
import math
from dataclasses import replace
from datetime import date
from types import MappingProxyType

import pytest

from services import sim_eval, sim_metrics, sim_season, sim_world
from tests import replay_fakes as fakes


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def test_spearman():
    assert sim_metrics.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert sim_metrics.spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert math.isnan(sim_metrics.spearman([1], [2]))
    # a block of ties shares its average rank, so tied zeros say nothing about the order of the rest
    assert sim_metrics.spearman([0, 0, 0, 0, 1, 2], [3, 1, 2, 0, 4, 5]) == pytest.approx(0.845, abs=1e-3)


def test_tiers_follow_adp_position_ranks(data):
    tiers = sim_metrics.tiers(data)
    ranks = sim_metrics.adp_ranks(data)
    assert all(tier == f"{data.players[pid].position}{(ranks[pid] - 1) // 12 + 1}" for pid, tier in tiers.items())
    assert {tier for tier in tiers.values()} >= {"QB1", "QB2", "RB1", "RB3", "WR3", "TE2"}


def test_weekly_and_season_metrics_cover_the_report(data):
    weekly = sim_metrics.weekly(data, range(10, 18))
    for key in ("RB1 q50", "25+ share %", "proj RMSE all", "proj rank r all", "sitting/wk RB", "team pts sd", "K mean"):
        assert key in weekly and not math.isnan(weekly[key])
    assert {"QB cmp %", "QB att/start", "QB INT/start"} <= set(weekly)
    season = sim_metrics.season(data)
    assert set(season) >= {"ADP vs finish r WR", "breakouts", "busts", "games missed / top-150"}
    assert season["ADP vs finish r RB"] > 0.5          # the fake season mostly plays to its ADP


def test_injury_metrics_count_new_injuries_per_game_played_and_missed_share(data):
    from services.replay_pool import Injury

    weeks = range(1, data.last_week + 1)
    before = sim_metrics.injuries(data, weeks)
    assert set(before) == {f"{name} {p}" for p in sim_metrics.SKILL for name in ("new inj/game", "missed share")}
    rb = min((p.adp, pid) for pid, p in data.players.items() if p.position == "RB" and p.adp)[1]
    games = [w for w in weeks if (rb, w) in data.actual]
    a, b = games[3], games[5]                         # he misses game a (any injury tag: a Q ruled out counts)...
    actual = {k: v for k, v in data.actual.items() if k not in {(rb, a), (rb, b)}}
    tagged = replace(data, actual=MappingProxyType(actual),
                     injuries=MappingProxyType({**data.injuries, (rb, a): Injury("Q", "DNP", "Knee"),
                                                (rb, b): Injury("SUS", None, "Suspension")}))   # ... b isn't one
    after = sim_metrics.injuries(tagged, weeks)
    assert after["new inj/game RB"] > before["new inj/game RB"]
    assert after["missed share RB"] > before["missed share RB"]
    assert all(after[k] == before[k] for k in before if not k.endswith("RB"))
    shares = [after[f"missed share {p}"] for p in sim_metrics.SKILL]
    assert all(0 <= s <= 1 for s in shares)
    assert set(sim_metrics.injuries(data, range(12, 18))) <= set(sim_metrics.weekly(data, range(12, 18)))


def test_injury_targets_scale_the_real_rates():
    from services import sim_injuries

    real = {"new inj/game RB": 0.06, "missed share RB": 0.16, "RB1 q50": 12.0}
    assert sim_eval.injury_targets(real) == pytest.approx(
        {"new inj/game RB": 0.06 * sim_injuries.INJURY_SCALE, "missed share RB": 0.16 * sim_injuries.INJURY_SCALE})


def test_the_mid_season_base_knows_nothing_from_the_cut_on(data):
    base = sim_eval.cut(data, 12)
    assert max(w for _, w in base.actual) == 11 and base.known_before is None
    assert base.schedule == data.schedule and base.last_week == data.last_week


def test_real_moves_are_read_from_the_next_seasons_games(data):
    belief = sim_world.estimate(data, data.last_week + 1)
    mover, gone, stays = fakes.player_id("WR", 3), fakes.player_id("RB", 60), fakes.player_id("QB", 1)
    new_team = next(t for t in belief.teams if t != belief.players[mover].team)
    games = {**data.team_by_week, mover: ((1, new_team), (2, new_team))}
    games.pop(gone)
    players = {pid: p for pid, p in data.players.items() if pid != gone}
    later = replace(data, team_by_week=MappingProxyType(games), players=MappingProxyType(players))

    moves = sim_eval.real_moves(belief, later)

    assert moves[mover] == new_team and moves[gone] is None and stays not in moves


def test_rookie_and_market_metrics(data):
    rookies = {fakes.player_id("WR", rank) for rank in range(1, 30)} | {fakes.player_id("QB", 1)}
    found = sim_metrics.rookies(data, rookies)
    assert found["rookie top-24 finishes"] > 0 and found["rookie QB wk1 starters"] in (0.0, 1.0)
    assert sim_metrics.adp_agreement(data, data)["ADP vs real ADP r (top-150)"] == pytest.approx(1.0)


def test_the_leaderboard_and_big_games_need_no_adp(data):
    board = sim_metrics.leaderboard(data)
    for position in sim_metrics.SKILL:
        ppg = [board[f"{position} top-{n} ppg"] for n in sim_metrics.LEADERS]
        assert ppg == sorted(ppg, reverse=True) and ppg[-1] > 0
    no_adp = replace(data, players=MappingProxyType({pid: replace(p, adp=None) for pid, p in data.players.items()}))
    assert sim_metrics.leaderboard(no_adp) == board
    weekly = sim_metrics.weekly(data, range(1, 18))
    assert weekly["25+ games"] >= weekly["30+ games"] >= weekly["35+ games"]
    assert sim_metrics.leaderboard(data, range(10, 18)) != board


def test_constants_are_read_only_overrides_apply_and_a_missing_file_says_how_to_fit_it(tmp_path, monkeypatch):
    from services import sim_constants, sim_eval

    with pytest.raises(TypeError):
        sim_world.params()["league"]["targets"] = 0.0
    monkeypatch.setattr(sim_constants, "_overrides", {})
    try:
        assert sim_eval.apply_overrides(["talent.role_sd=0.5"]) == {"talent": {"role_sd": 0.5}}
        assert sim_world.params()["talent"]["role_sd"] == 0.5
    finally:
        monkeypatch.undo()
        sim_world.params.cache_clear()
    assert sim_world.params()["talent"].get("role_sd") != 0.5
    with pytest.raises(RuntimeError, match="nothing.json.*sim_eval --fit"):
        sim_constants.fitted_json(tmp_path / "nothing.json", "python -m services.sim_eval --fit", ())
    (tmp_path / "part.json").write_text("{}")
    with pytest.raises(RuntimeError, match="part.json has no league"):
        sim_constants.fitted_json(tmp_path / "part.json", "x", ("league",))


def fake_case(case: tuple[str, int], seeds: list[int], session=None, offseason=None) -> tuple[dict, dict]:
    """sim_eval._case on the fake season (no database), whatever the mode: the season number shifts the seeds;
    the real side reports season / 10 (its mean depends on the order it is summed in) and the sim side the
    role_sd and the number of draft picks the process sees, to show the overrides and offseason reach workers."""
    _, season = case
    offseason = sim_eval._worker["offseason"] if session is None else offseason    # as _case: a worker's own
    data = fakes.fake_season()
    weeks = range(sim_eval.MID_WEEK, data.last_week + 1)
    base = sim_eval.cut(data, sim_eval.MID_WEEK)
    sims = [sim_eval._rest(sim_season.simulate(base, 10 * season + seed, sim_eval.MID_WEEK), weeks) for seed in seeds]
    seen = {"role_sd": sim_world.params()["talent"]["role_sd"], "picks": float(len(offseason["picks"]))}
    return {**sim_eval._rest(data, weeks), "season": season / 10}, {**sim_eval._mean(sims), **seen}


def kind_case(case: tuple[str, int], seeds: list[int], session=None, offseason=None) -> tuple[dict, dict]:
    """A case that only says which mode it ran as."""
    is_mid = {"is_mid": float(case[0] == "mid")}
    return is_mid, is_mid


def failing_case(case: tuple[str, int], seeds: list[int], session=None, offseason=None) -> tuple[dict, dict]:
    """A case that fails for season 2."""
    if case[1] == 2:
        raise RuntimeError("season 2 broke")
    return kind_case(case, seeds, session, offseason)


@pytest.mark.parametrize("workers", [1, 2])
def test_a_failing_case_fails_the_run_never_a_partial_report(monkeypatch, workers):
    monkeypatch.setattr(sim_eval, "_offseason", lambda *_: {"picks": [], "first": {}})
    with pytest.raises(RuntimeError, match="season 2 broke"):
        sim_eval.run([1, 2, 3], [1], "mid", workers, {}, case=failing_case)


@pytest.mark.parametrize("workers", [1, 2])
def test_both_modes_report_each_mode_in_its_own_section(monkeypatch, workers):
    monkeypatch.setattr(sim_eval, "_offseason", lambda *_: {"picks": [], "first": {}})
    report = sim_eval.run([1, 2], [1], "both", workers, {}, case=kind_case)

    assert report["mid"]["real"]["is_mid"] == report["mid"]["sim"]["is_mid"] == 1.0
    assert report["full"]["real"]["is_mid"] == report["full"]["sim"]["is_mid"] == 0.0


def test_the_parallel_report_equals_the_serial_one_and_workers_get_the_overrides(monkeypatch):
    from services import sim_constants

    monkeypatch.setattr(sim_constants, "_overrides", {})
    monkeypatch.setattr(sim_eval, "_offseason", lambda *_: {"picks": [{}] * 7, "first": {}})
    try:
        overrides = sim_eval.apply_overrides(["talent.role_sd=0.41"])
        serial = sim_eval.run([1, 2, 3], [1], "full", 1, overrides, case=fake_case)
        parallel = sim_eval.run([1, 2, 3], [1], "full", 2, overrides, case=fake_case)
    finally:
        monkeypatch.undo()
        sim_world.params.cache_clear()
    assert parallel == serial
    assert serial["full"]["real"]["season"] == pytest.approx(0.2)    # order is proven by parallel == serial
    assert serial["full"]["sim"]["role_sd"] == 0.41 and serial["full"]["sim"]["picks"] == 7


def test_draft_picks_are_cached_and_downloaded_again_only_when_needed(tmp_path, monkeypatch):
    from services import sim_draft_picks as fit

    calls: list[list[int]] = []

    def download(seasons: list[int]) -> list[dict]:
        calls.append(seasons)
        return [{"season": s, "pick": p, "team": "KC", "gsis_id": f"{s}-{p}", "position": "WR",
                 "pfr_player_name": "A", "college": None} for s in seasons for p in (1, 2)]

    monkeypatch.setattr(fit, "PICKS_CACHE", tmp_path / "cache" / "draft_picks.json")
    monkeypatch.setattr(fit, "_download", download)
    monkeypatch.setattr(fit, "_today", lambda: date(2023, 5, 1))

    first = fit.draft_picks(range(2020, 2023))
    assert calls == [[2020, 2021, 2022]] and len(first) == 6 and first[0]["college"] == "-"
    assert fit.draft_picks([2021]) == [r for r in first if r["season"] == 2021] and len(calls) == 1
    assert fit.draft_picks(range(2020, 2023)) == first and len(calls) == 1
    fit.draft_picks([2023])                                 # a season not in the file: download it and the rest
    assert calls[-1] == [2020, 2021, 2022, 2023]
    fit.draft_picks([2023])                                 # drafted, but in May: its ids may not be in yet
    assert len(calls) == 3
    monkeypatch.setattr(fit, "_today", lambda: date(2023, 9, 1))
    fit.draft_picks([2023])
    fit.draft_picks([2023])                                 # settled from September on
    assert len(calls) == 4
    fit.draft_picks([2021], refresh=True)
    assert calls[-1] == [2021] and len(calls) == 5
    fit.PICKS_CACHE.write_text("null")                      # unreadable: download again
    fit.draft_picks([2021])
    assert len(calls) == 6
    for wrong in ('{"downloaded": "2023-09-01", "rows": [{"pick": 1}]}', '{}', '[{"season'):   # wrong shape or cut off
        fit.PICKS_CACHE.write_text(wrong)
        fit.draft_picks([2021])
    assert len(calls) == 9 and not fit.PICKS_CACHE.with_suffix(".tmp").exists()


def test_passing_reads_each_team_games_passing_leader_and_skips_unrecorded_attempts(data):
    from dataclasses import replace
    from types import MappingProxyType

    from services.replay_box import BoxLine
    qbs = [pid for pid, p in data.players.items() if p.position == "QB"][:2]
    teams = {pid: data.team_at(pid, 1) for pid in qbs}
    backup = next(pid for pid, p in data.players.items() if p.position == "QB" and pid not in qbs)
    box = {(qbs[0], 1): BoxLine(pass_cmp=20, pass_att=30, pass_yds=250, pass_int=1),
           (qbs[1], 1): BoxLine(pass_cmp=30, pass_att=40, pass_yds=300),
           (backup, 1): BoxLine(pass_cmp=5, pass_att=8, pass_yds=40),          # not his team's passing leader
           (qbs[0], 2): BoxLine(pass_yds=260)}                                 # an old save: no attempts
    by_week = {**data.team_by_week, backup: ((1, teams[qbs[1]]),)}
    season = replace(data, box=MappingProxyType(box), team_by_week=MappingProxyType(by_week))

    shown = sim_metrics.passing(season, [1, 2])

    assert shown == {"QB cmp %": pytest.approx(100 * 50 / 70), "QB att/start": 35.0,
                     "QB INT/start": pytest.approx(1 / 3)}          # INTs count the old save's start too
    assert math.isnan(sim_metrics.passing(season, [2])["QB cmp %"])
    assert sim_metrics.passing(season, [2])["QB INT/start"] == 0.0
