"""Injury layer, recency model, adjustments/explanations, flags, waivers, trade finder, backtest."""
import json
from datetime import datetime, timedelta, timezone

import httpx
import polars as pl
import pytest
from sqlalchemy.orm import Session

from database import populate_stats as populate
from database.models import (
    DepthChartEntry, InjuryReport, NflGame, Player, PlayerStatsWeekly, PlayerStatus, WeeklySourceProjection,
)
from integrations import sleeper
from services import backtest, injuries
from services import valuation as valuation_module
from services.flags import player_flags
from services.injuries import Availability, Boost, InjuryContext
from services.league import LeagueSettings
from services.projections import GameLine, PlayerSeason
from services.schedule_strength import Game, Schedule
from services.trade_finder import find_trades
from services.valuation import build_valuation
from services.waivers import faab_label, waiver_candidates
from services.weekly_adjust import adjust
from services.weekly_model import Matchup, weekly_rate
from services.weekly_rankings import build_weekly_rankings

SEASON, WEEK = 2026, 7


def line(week, points, xp=None, team="AAA", snap=None, tds=0, xtds=None):
    return GameLine(week=week, season_type="REG", opponent="OPP", fantasy_points=points, expected_points=xp,
                    expected_tds=xtds, passing_yards=0, passing_tds=0, interceptions=0, carries=10,
                    rushing_yards=50, rushing_tds=tds, targets=3, receptions=2, receiving_yards=20,
                    receiving_tds=0, target_share=None, snap_pct=snap)


def season(pid, name, position, games, team="AAA"):
    return PlayerSeason(pid, name, position, team, SEASON, tuple(games))


# ---------- play probability and availability ----------

def test_play_probability_mapping():
    rates = dict(injuries.DEFAULT_RATES)
    assert injuries.play_probability(rates, None, None) == 1.0
    assert injuries.play_probability(rates, "Out", "LP") == 0.0
    assert injuries.play_probability(rates, "IR", None) == 0.0
    assert injuries.play_probability(rates, "Questionable", "DNP") == 0.49
    assert injuries.play_probability(rates, "Questionable", None) == 0.75
    assert injuries.play_probability(rates, "Weird", None) == 0.75


def _seed_history(session: Session, ids: dict) -> None:
    """2025 reports: 20 Q+LP players, 15 of whom played; 20 healthy FP players, all played."""
    rows, stats = [], []
    names = [f"WR Player {i:02d}" for i in range(1, 41)]
    pids = [session.query(Player.id).filter(Player.full_name == n).scalar() for n in names]
    for i, pid in enumerate(pids):
        status, practice = ("Questionable", "LP") if i < 20 else (None, "FP")
        rows.append(InjuryReport(season=2025, week=5, player_id=pid, team="AAA", report_status=status,
                                 practice_status=practice))
        if i >= 15:      # weeks 4 and 5 exist for most; drop week 5 for 5 of the questionable ones
            continue
        stats.append(PlayerStatsWeekly)
    session.add_all(rows)
    # PRIOR season (2025) has weeks 1-17 for every pool player already; remove week 5 for 5 Q players.
    for pid in pids[15:20]:
        session.query(PlayerStatsWeekly).filter_by(player_id=pid, season=2025, week=5).delete()
    session.commit()


def test_learned_play_rates_normalize_to_healthy(session, seeded):
    _seed_history(session, seeded)
    # Postseason rows (week 19+) can never match regular-season stats; they must not count.
    pid = session.query(Player.id).filter(Player.full_name == "WR Player 01").scalar()
    session.add(InjuryReport(season=2025, week=19, player_id=pid, team="AAA", report_status="Questionable",
                             practice_status="LP"))
    session.commit()
    injuries._rate_cache.clear()
    rates = injuries.learned_play_rates(session)
    assert rates[("QUESTIONABLE", "LP")] == pytest.approx(0.75)
    assert injuries.learned_play_rates(session, exclude_season=2025) == injuries.DEFAULT_RATES   # left out
    assert rates[("NONE", "FP")] == 1.0
    assert rates[("OUT", "*")] == 0.0


def test_availability_prefers_fresh_sleeper_and_ignores_rest_days(session, seeded):
    injuries._rate_cache.clear()
    a, b, c = seeded["rb1"], seeded["rb2"], seeded["chase"]
    session.add_all([
        InjuryReport(season=SEASON, week=WEEK, player_id=a, team="AAA", report_status=None, practice_status="LP", injury="Ankle"),
        InjuryReport(season=SEASON, week=WEEK, player_id=b, team="AAA", report_status=None, practice_status="DNP",
                     injury="Not injury related - resting player"),
        PlayerStatus(player_id=a, injury_status="Questionable", injury_body_part="Ankle"),
        PlayerStatus(player_id=c, injury_status="Out", fetched_at=datetime.now(timezone.utc) - timedelta(days=3)),
    ])
    session.commit()
    avail = injuries.load_availability(session, SEASON, WEEK)
    assert (avail[a].status, avail[a].source, avail[a].practice) == ("Questionable", "sleeper", "LP")
    assert avail[a].play_prob == injuries.DEFAULT_RATES[("QUESTIONABLE", "LP")]
    assert "LP in practice" in avail[a].label
    assert avail[b].play_prob == 1.0 and avail[b].practice is None          # rest day isn't an injury
    assert c not in avail                                                    # stale Sleeper data ignored
    assert injuries.load_availability(session, SEASON, WEEK, use_live=False)[a].source == "report"


# ---------- vacated opportunity ----------

def aaa_schedule(weeks=range(1, 7), home="AAA", away="OPP", final=True) -> Schedule:
    games = tuple(Game(week=w, home_team=home, away_team=away, home_score=20 if final else None,
                       away_score=17 if final else None, spread_line=None, total_line=None) for w in weeks)
    opponents = {}
    for g in games:
        opponents[(g.home_team, g.week)] = g.away_team
        opponents[(g.away_team, g.week)] = g.home_team
    return Schedule(games=games, opponents=opponents)


def _backfield():
    starter = season(1, "Breece Starter", "RB", [line(w, 18, xp=18) for w in (1, 2, 3, 6)])
    backup = season(2, "Braelon Backup", "RB", [line(w, 14 if w in (4, 5) else 6, xp=15 if w in (4, 5) else 6)
                                                for w in range(1, 7)])
    third = season(3, "Third Back", "RB", [line(w, 3, xp=3) for w in range(1, 7)])
    receiver = season(4, "Slot Guy", "WR", [line(w, 10, xp=10) for w in range(1, 7)])
    return [starter, backup, third, receiver]


def test_vacated_boosts_use_with_without_history_and_depth_chart():
    seasons = _backfield()
    rates = {1: 18.0, 2: 7.0, 3: 3.0, 4: 10.0}
    avail = {1: Availability(1, 0.5, "Questionable", "DNP", "Quad", "sleeper")}
    boosts = injuries.vacated_boosts(seasons, rates, avail, {1: ("RB", 1), 2: ("RB", 2), 3: ("RB", 3)},
                                     aaa_schedule(), 7)
    backup = boosts[2][0]
    assert backup.from_name == "Breece Starter" and "with/without (2 games" in backup.method
    # measured +9 expected pts without him (15 vs 6), shrunk 2/(2+2) toward the depth-chart heuristic
    heuristic = 18.0 * 0.75 * (0.6 + 0.4 * 7 / 10)
    assert backup.full_points == pytest.approx(0.5 * 9 + 0.5 * heuristic, abs=0.01)
    assert backup.expected_points == pytest.approx(backup.full_points * 0.5, abs=0.01)
    assert boosts[3][0].full_points < backup.full_points                    # his usage didn't change without him
    assert boosts[4][0].full_points == pytest.approx(18.0 * 0.07, abs=0.01)  # cross-position trickle
    assert injuries.vacated_boosts(seasons, rates, {}, {}, aaa_schedule(), 7) == {}   # nobody hurt -> nothing moves


def test_next_man_up_is_the_player_behind_the_absent_one():
    seasons = _backfield()
    rates = {1: 18.0, 2: 7.0, 3: 3.0, 4: 10.0}
    avail = {2: Availability(2, 0.0, "Out", "DNP", "Knee", "report")}           # the RB2 is out, not the starter
    depth = {1: ("RB", 1), 2: ("RB", 2), 3: ("RB", 3)}
    boosts = injuries.vacated_boosts(seasons, rates, avail, depth, aaa_schedule(), 7)
    assert boosts[3][0].full_points > boosts.get(1, (Boost(1, 2, "", "", 0, 0, ""),))[0].full_points


def test_with_without_ignores_games_for_a_former_team():
    starter = season(1, "Starter", "RB", [line(w, 18, xp=18) for w in (1, 2, 3, 6)])
    # Weeks 1-3 were played for another team (opponent BBB plays OTH); only weeks 4-6 are with AAA.
    traded = PlayerSeason(2, "Traded", "RB", "AAA", SEASON, tuple(
        [replace_opp(line(w, 20, xp=20), "BBB") for w in (1, 2, 3)] + [line(w, 6, xp=6) for w in (4, 5, 6)]))
    schedule = aaa_schedule(range(4, 7))
    other = aaa_schedule(range(1, 4), home="OTH", away="BBB")
    merged = Schedule(games=schedule.games + other.games, opponents={**schedule.opponents, **other.opponents})
    assert injuries.with_without(traded, starter, "AAA", merged, 7) == (0.0, 2)       # weeks 4-5 without, 6 with


def replace_opp(game, opponent):
    from dataclasses import replace
    return replace(game, opponent=opponent)


def test_returning_competition_flags_fill_ins():
    seasons = _backfield()
    seasons[0] = season(1, "Breece Starter", "RB", [line(w, 18, xp=18) for w in (1, 2, 3)])   # missed 4-6
    back = {1: Availability(1, 1.0, None, "FP", "Quad", "report")}
    returning = injuries.returning_competition(seasons, back, 7, aaa_schedule())
    (back_starter,) = returning[2]
    assert back_starter.name == "Breece Starter" and 4 not in returning
    assert back_starter.expected_loss == pytest.approx(6.0) and back_starter.games_without == 3  # 12 -> 6 xp gain
    assert returning[3][0].expected_loss == 0.0                                  # no gain without him
    out = {1: Availability(1, 0.0, "Out", "DNP", "Quad", "report")}
    assert injuries.returning_competition(seasons, out, 7, aaa_schedule()) == {}
    assert injuries.returning_competition(seasons, {}, 7, aaa_schedule()) == {}      # no report: maybe released


# ---------- recency and adjustments ----------

def test_weekly_rate_weights_recent_games_and_shrinks_to_prior():
    player = season(1, "P", "WR", [line(1, 5, xp=5), line(2, 5, xp=5), line(3, 25, xp=25)])
    flat, recent = weekly_rate(player, None, None), weekly_rate(player, None, 1.0)
    assert flat == pytest.approx(11.67, abs=0.01) and recent > flat
    assert weekly_rate(player, 30.0, None) == pytest.approx(3 / 7 * 11.6667 + 4 / 7 * 30, abs=0.02)
    assert weekly_rate(season(2, "Q", "WR", []), 12.0) == 12.0


def _matchup():
    return Matchup("OPP", True, sos_factor=1.0, sos_rank=16, implied_total=None, team_ppg=None, game_final=False)


def test_adjust_applies_availability_boosts_scenarios_and_range(monkeypatch):
    import services.weekly_adjust as wa

    monkeypatch.setattr(wa, "range_for", lambda position, if_active: {"floor": 0.3, "ceiling": 2.0})
    boost = Boost(2, 1, "Starter", "Questionable", full_points=8.0, expected_points=4.0, method="x")
    out_boost = Boost(2, 3, "Other", "Out", full_points=4.0, expected_points=4.0, method="x")
    context = InjuryContext(availability={1: Availability(1, 0.5, "Questionable", "LP", None, "sleeper"),
                                          3: Availability(3, 0.0, "Out", None, None, "report")},
                            boosts={2: (boost, out_boost)})
    result = adjust("RB", blended=10.0, tilt=1.0, matchup=_matchup(), model_share=0.25, player_id=2, context=context)
    assert result.boost_points == pytest.approx(4.0 + 4.0 * 0.25)        # Out teammate: only the model's share
    assert result.if_active == result.projection == pytest.approx(15.0)
    assert (result.floor, result.ceiling) == (4.5, 30.0)
    (scenario,) = result.scenarios                                        # Out teammate is already priced in
    assert (scenario.teammate, scenario.extra_points, scenario.projection) == ("Starter", 4.0, 19.0)
    hurt = adjust("RB", 10.0, 1.0, _matchup(), 0.25, 1, context)
    assert hurt.projection == 5.0 and hurt.floor == 0.0                   # may sit: floor 0


# ---------- flags ----------

def test_flags_cover_opportunity_returning_role_td_and_injury():
    games = [line(w, 30 if w > 3 else 10, xp=12 if w <= 3 else 16, snap=50 if w <= 3 else 70, tds=1 if w > 3 else 0,
                  xtds=0.1) for w in range(1, 7)]
    player = season(2, "Backup", "RB", games)
    context = InjuryContext(
        availability={2: Availability(2, 0.8, "Questionable", "LP", "Knee", "report")},
        boosts={2: (Boost(2, 1, "Starter", "Questionable", 7.5, 2.0, "with/without (3 games)"),)},
        returning={2: (injuries.Returning("Old Starter", 3.0, 2),)})
    keys = {f.key: f for f in player_flags(player, context)}
    assert set(keys) >= {"opportunity", "competition_returning", "role_growth", "td_luck", "efficiency", "injury"}
    assert keys["opportunity"].label == "+7.5 if Starter sits" and keys["opportunity"].kind == "breakout"
    assert keys["td_luck"].direction == "down" and keys["injury"].label == "80% to play"
    assert "3.0 expected pts/game" in keys["competition_returning"].detail


def test_backup_qb_returning_does_not_flag_the_starter():
    starter = season(1, "Sam Starter", "QB", [line(w, 20, xp=20) for w in range(1, 7)])
    backup = season(2, "Drew Backup", "QB", [line(w, 12, xp=12) for w in (1, 2)])         # missed 3-6
    back = {2: Availability(2, 1.0, None, "FP", "Hip", "report")}
    assert injuries.returning_competition([starter, backup], back, 7, aaa_schedule()) == {}
    fill_in = season(3, "Fill In", "QB", [line(w, 14, xp=14) for w in (5, 6)])
    real_starter = season(4, "Real Starter", "QB", [line(w, 22, xp=22) for w in (1, 2, 3, 4)])
    back = {4: Availability(4, 1.0, None, "FP", "Hand", "report")}
    flagged = injuries.returning_competition([fill_in, real_starter], back, 7, aaa_schedule())
    assert flagged[3][0].name == "Real Starter"


def test_returning_starter_takes_back_the_models_share(monkeypatch):
    import services.weekly_adjust as wa

    monkeypatch.setattr(wa, "range_for", lambda position, if_active: None)
    context = InjuryContext(availability={}, returning={2: (injuries.Returning("Starter", 8.0, 3),)})
    result = adjust("RB", blended=10.0, tilt=1.0, matchup=_matchup(), model_share=0.25, player_id=2, context=context)
    assert result.projection == pytest.approx(8.0)                        # 10 - 8 x 0.25


def test_snap_growth_and_efficient_part_timer_flags():
    growing = season(5, "Rising Back", "RB", [line(w, 8, xp=8, snap=s) for w, s in zip(range(1, 7), (20, 25, 35, 45, 52, 61))])
    assert "snap_growth" in {f.key for f in player_flags(growing, InjuryContext(availability={}))}
    flat = season(6, "Flat Back", "RB", [line(w, 8, xp=8, snap=60) for w in range(1, 7)])
    assert "snap_growth" not in {f.key for f in player_flags(flat, InjuryContext(availability={}))}
    efficient = season(7, "Part Timer", "WR", [line(w, 9, xp=9, snap=45) for w in range(1, 6)])   # 17 pts at 85%
    assert "efficient_part_timer" in {f.key for f in player_flags(efficient, InjuryContext(availability={}))}
    busy = season(8, "Busy WR", "WR", [line(w, 9, xp=9, snap=90) for w in range(1, 6)])
    assert "efficient_part_timer" not in {f.key for f in player_flags(busy, InjuryContext(availability={}))}


# ---------- weekly integration, waivers, trade finder, API ----------

def _seed_week(session: Session) -> dict[str, int]:
    for week in range(1, 7):
        session.add(NflGame(game_id=f"g{week}", season=SEASON, week=week, game_type="REG", home_team="AAA",
                            away_team="OPP", home_score=24, away_score=20, spread_line=3.0, total_line=44.0))
    session.add(NflGame(game_id="g7", season=SEASON, week=WEEK, game_type="REG", home_team="AAA", away_team="BBB",
                        spread_line=4.0, total_line=50.0))
    ids = {n: session.query(Player.id).filter(Player.full_name == n).scalar()
           for n in ("RB Player 01", "RB Player 02", "WR Player 40", "WR Player 01")}
    for name, pid in ids.items():
        session.add(WeeklySourceProjection(season=SEASON, week=WEEK, source="espn", player_id=pid, player_name=name,
                                           position=name.split()[0], team="AAA", pct_rostered=12.0 if "40" in name else 99.0,
                                           stats=json.dumps({"rush_yds": 70.0, "rec": 3.0, "rec_yds": 25.0})))
    session.add(PlayerStatus(player_id=ids["RB Player 01"], injury_status="Questionable", injury_body_part="Quad"))
    session.add(InjuryReport(season=SEASON, week=WEEK, player_id=ids["RB Player 01"], team="AAA",
                             report_status=None, practice_status="DNP"))
    session.add(DepthChartEntry(team="AAA", player_id=ids["RB Player 01"], position="RB", depth=1))
    session.add(DepthChartEntry(team="AAA", player_id=ids["RB Player 02"], position="RB", depth=2))
    session.commit()
    injuries._rate_cache.clear()
    valuation_module._cache.clear()
    return ids


def test_past_weeks_ignore_live_status(session, seeded):
    ids = _seed_week(session)
    session.add(NflGame(game_id="g8", season=SEASON, week=8, game_type="REG", home_team="AAA", away_team="CCC"))
    session.commit()
    past = {p.player_id: p for p in build_weekly_rankings(session, LeagueSettings(), week=6).players}
    assert ids["RB Player 01"] not in past or past[ids["RB Player 01"]].play_prob == 1.0


def test_weekly_rankings_carry_injury_status_scenarios_and_explanations(session, seeded):
    ids = _seed_week(session)
    by_id = {p.player_id: p for p in build_weekly_rankings(session, LeagueSettings()).players}
    hurt, backup = by_id[ids["RB Player 01"]], by_id[ids["RB Player 02"]]
    assert hurt.play_prob == pytest.approx(injuries.DEFAULT_RATES[("QUESTIONABLE", "DNP")])
    assert hurt.projection == pytest.approx(hurt.if_active * hurt.play_prob, abs=0.02)
    assert hurt.status.startswith("Questionable (Quad)")
    assert backup.boost_points > 0 and backup.scenarios[0].teammate == "RB Player 01"
    labels = [s.label for s in backup.explanation]
    assert labels[0] == "Recent production" and "If RB Player 01 sits" in labels and labels[-1] == "Projection"


def test_waivers_and_trade_finder(session, seeded):
    ids = _seed_week(session)
    league = LeagueSettings()
    rankings = build_weekly_rankings(session, league)
    valuation = build_valuation(session, league)
    candidates = waiver_candidates(rankings, valuation, max_rostered=50)
    assert [c.player.player_id for c in candidates] == [ids["WR Player 40"]] or all(
        c.pct_rostered <= 50 for c in candidates)
    if candidates:
        assert 1 <= candidates[0].faab_pct <= 40 and candidates[0].reasons[0].startswith("Rostered in 12%")
    assert [faab_label(p) for p in (30, 12, 5, 1)] == ["Priority add", "Solid add", "Depth add", "Speculative stash"]

    offers = find_trades(valuation, [ids["WR Player 01"]], want_position="RB")
    assert offers and all(o.get.position == "RB" for o in offers)
    assert all(o.evaluation.verdict in ("fair", "slight_win", "slight_loss") for o in offers)
    assert len({o.get.player_id for o in offers}) == len(offers)
    assert find_trades(valuation, [999999]) == []
    dupes = find_trades(valuation, [ids["WR Player 01"], ids["WR Player 01"]], want_position="RB")
    assert [o.get.player_id for o in dupes] == [o.get.player_id for o in offers]       # duplicates ignored


def test_new_endpoints(client, engine):
    with Session(engine) as db:
        ids = _seed_week(db)
    rb1, rb2 = ids["RB Player 01"], ids["RB Player 02"]
    compare = client.get("/api/rankings/weekly", params={"ids": f"{rb1},{rb2}"}).json()
    assert {r["player"]["id"] for r in compare["results"]} == {rb1, rb2}
    row = next(r for r in compare["results"] if r["player"]["id"] == rb1)
    assert {"floor", "ceiling", "play_prob", "status", "scenarios", "flags", "if_active"} <= set(row)
    assert client.get("/api/rankings/weekly", params={"ids": "x"}).status_code == 422

    explain = client.get(f"/api/rankings/weekly/{rb2}/explain").json()
    assert explain["steps"][-1]["label"] == "Projection" and explain["player"]["id"] == rb2
    assert client.get("/api/rankings/weekly/999999/explain").status_code == 404

    flags = client.get("/api/insights/flags", params={"kind": "risk"}).json()
    assert any(r["player"]["id"] == rb1 for r in flags["results"])
    assert client.get("/api/insights/flags", params={"kind": "nope"}).status_code == 422

    waivers = client.get("/api/waivers", params={"max_rostered": 50}).json()
    assert waivers["max_rostered"] == 50 and all(r["pct_rostered"] <= 50 for r in waivers["results"])

    found = client.post("/api/trade/find", json={"my_players": [ids["WR Player 01"]], "want_position": "RB"}).json()
    assert found["results"] and found["results"][0]["get"]["position"] == "RB"


# ---------- data loading ----------

def test_practice_codes_and_injury_loader(session, seeded, monkeypatch):
    assert [populate.practice_code(t) for t in ("Did Not Participate In Practice", "Limited Participation in Practice",
                                                "Full Participation in Practice", None)] == ["DNP", "LP", "FP", None]
    gsis = session.get(Player, seeded["chase"]).gsis_id
    frame = pl.DataFrame([{"season": 2026, "week": 7, "gsis_id": gsis, "team": "CIN", "position": "WR",
                           "report_status": "Questionable", "practice_status": "Limited Participation in Practice",
                           "report_primary_injury": "Hamstring", "practice_primary_injury": None, "extra": 1},
                          {"season": 2026, "week": 7, "gsis_id": gsis, "team": "CIN", "position": "OL",
                           "report_status": None, "practice_status": None, "report_primary_injury": None,
                           "practice_primary_injury": None, "extra": 2}])
    monkeypatch.setattr(populate.nfl, "load_injuries", lambda seasons: frame)
    populate.load_injury_reports(session, [2026], populate.PlayerIndex(session))
    (row,) = session.query(InjuryReport).all()
    assert (row.report_status, row.practice_status, row.injury) == ("Questionable", "LP", "Hamstring")


def test_depth_chart_loader_keeps_latest_skill_snapshot(session, seeded, monkeypatch):
    gsis = session.get(Player, seeded["rb2"]).gsis_id
    frame = pl.DataFrame([
        {"dt": "2026-10-20", "team": "AAA", "gsis_id": gsis, "pos_abb": "RB", "pos_rank": 1},
        {"dt": "2026-10-23", "team": "AAA", "gsis_id": gsis, "pos_abb": "RB", "pos_rank": 2},
        {"dt": "2026-10-23", "team": "AAA", "gsis_id": gsis, "pos_abb": "LT", "pos_rank": 1}])
    monkeypatch.setattr(populate.nfl, "load_depth_charts", lambda season: frame)
    populate.load_depth_charts(session, 2026, populate.PlayerIndex(session))
    (row,) = session.query(DepthChartEntry).all()
    assert (row.position, row.depth, row.as_of) == ("RB", 2, "2026-10-23")


def test_sleeper_status_refresh(session, seeded):
    session.query(Player).filter(Player.id == seeded["rb1"]).update({"sleeper_player_id": "4034"})
    session.commit()
    payload = {"4034": {"injury_status": "Questionable", "injury_body_part": "Quad", "team": "AAA",
                        "depth_chart_position": "RB", "depth_chart_order": 1}, "999": {"injury_status": "Out"}}
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload)))
    assert sleeper.refresh_status(session, client) == 1
    status = session.get(PlayerStatus, seeded["rb1"])
    assert (status.injury_status, status.depth_order) == ("Questionable", 1)
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[1])))
    with pytest.raises(sleeper.SleeperError):
        sleeper.refresh_status(session, bad)


# ---------- backtest ----------

def test_backtest_scoring_and_ranges():
    targets = [backtest.Target(5, i, "WR", actual=float(i), projected=float(21 - i)) for i in range(1, 21)]
    targets += [backtest.Target(5, 100 + i, "RB", actual=float(i), projected=float(i)) for i in range(1, 11)]
    metrics = backtest.score(targets)
    assert metrics["n"] == 30 and metrics["rank_corr"] == pytest.approx(0.0)      # WR -1, RB +1
    many = [backtest.Target(w, i, "TE", actual=float(i % 20 + 1), projected=10.0, if_active=10.0)
            for w in range(4, 9) for i in range(20)]
    ranges = backtest.ranges(many)
    assert set(ranges) == {"TE"} and ranges["TE"]["floor"] < 1 < ranges["TE"]["ceiling"]


def test_backtest_runs_on_a_seeded_season(session, seeded):
    for week in range(1, 8):
        session.add(NflGame(game_id=f"b{week}", season=2026, week=week, game_type="REG", home_team="AAA",
                            away_team="OPP", home_score=20 if week < 7 else None, away_score=17 if week < 7 else None,
                            spread_line=2.0, total_line=44.0))
    session.commit()
    injuries._rate_cache.clear()
    data = backtest.load_season(session, 2026)
    targets = backtest.run(session, data, backtest.WeeklyModelSettings())
    assert targets and {t.week for t in targets} <= set(range(4, 8))
    assert all(t.projected > 0 and t.if_active >= t.projected for t in targets)
    # Teams come from the games before each week, not from today's roster.
    moved = data.current[0]
    assert backtest.truncate(moved, 4, data.schedule).team == "AAA"


def test_range_for_picks_the_projection_tier(monkeypatch):
    import services.weekly_model as wm

    monkeypatch.setattr(wm, "projection_ranges", lambda: {"WR": {"floor": 0.2, "ceiling": 1.8, "tiers": [
        {"min": 0.0, "max": 16.0, "floor": 0.22, "ceiling": 1.9}, {"min": 16.0, "max": 999.0, "floor": 0.4, "ceiling": 1.5}]}})
    assert wm.range_for("WR", 22.0)["floor"] == 0.4          # a star keeps a real floor
    assert wm.range_for("WR", 9.0)["floor"] == 0.22
    assert wm.range_for("TE", 9.0) is None


def test_returning_starter_trims_the_fill_ins_rest_of_season_rate():
    seasons = _backfield()
    seasons[0] = season(1, "Breece Starter", "RB", [line(w, 18, xp=18) for w in (1, 2, 3)])   # missed 4-6
    losses = injuries.ros_role_losses(seasons, 7, aaa_schedule(), {1: 0.8})
    name, loss = losses[2]                    # backup: +6 xp/game in 3 of his 6 games, starter plays 80%
    assert name == "Breece Starter" and loss == pytest.approx(6.0 * 3 / 6 * 0.8)
    assert injuries.ros_role_losses(seasons, 7, aaa_schedule(), {1: 0.0}) == {}     # out for the year: role stays
    assert injuries.ros_role_losses(seasons, 7, aaa_schedule(), {}) == {}           # no status: released?


def test_trade_finder_keeps_offers_fair_by_market_value(session, seeded):
    from dataclasses import replace as dc_replace

    from services import trade_finder

    _seed_week(session)
    valuation = build_valuation(session, LeagueSettings())
    player = valuation.values[0]
    assert trade_finder.market_value(dc_replace(player, expert_ros_ppg=None), valuation).trade_value == player.trade_value
    rated = trade_finder.market_value(dc_replace(player, expert_ros_ppg=30.0, availability=0.5), valuation)
    assert rated.ros_ppg == 15.0

    mine = valuation.by_id[valuation.values[3].player_id]
    offers = trade_finder.find_trades(valuation, [mine.player_id])
    assert offers
    for offer in offers:
        edge = trade_finder._market_percent(offer.give, offer.get, valuation)
        assert trade_finder.MIN_MARKET_EDGE <= edge <= trade_finder.MAX_MARKET_EDGE
        assert offer.evaluation.percent_difference <= trade_finder.MAX_PERCENT_GAIN
