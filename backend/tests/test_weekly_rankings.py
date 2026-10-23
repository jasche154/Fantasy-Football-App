"""Adjusted strength of schedule, the rest-of-season schedule factor, and Week X rankings."""
import json

import pytest
from sqlalchemy.orm import Session

from database.models import NflGame, Player, WeeklySourceProjection
from services import valuation as valuation_module
from services.league import LeagueSettings
from services.projections import GameLine, PlayerSeason
from services.scoring import scoring_for
from services.schedule_strength import Game, Schedule, defense_strength
from services.valuation import build_valuation, value_players
from services.weekly_model import Matchup, model_stats, points_from_stats, props_line
from services.weekly_rankings import WEIGHTS, _rank_to_points, build_weekly_rankings

SEASON, WEEK = 2026, 7
PPR = scoring_for("ppr")


def line(week: int, opponent: str, points: float, **stats) -> GameLine:
    base = dict(week=week, season_type="REG", opponent=opponent, fantasy_points=points, expected_points=None,
                expected_tds=None, passing_yards=0, passing_tds=0, interceptions=0, carries=0,
                rushing_yards=0, rushing_tds=0, targets=0, receptions=0, receiving_yards=0,
                receiving_tds=0, target_share=None, snap_pct=None)
    return GameLine(**{**base, **stats})


def schedule_of(*games: tuple[int, str, str]) -> Schedule:
    built = tuple(Game(week=w, home_team=h, away_team=a, home_score=20, away_score=17,
                       spread_line=None, total_line=None) for w, h, a in games)
    opponents = {}
    for g in built:
        opponents[(g.home_team, g.week)] = g.away_team
        opponents[(g.away_team, g.week)] = g.home_team
    return Schedule(games=built, opponents=opponents)


# ---- Strength of schedule ------------------------------------------------------------------

def test_defense_strength_adjusts_for_the_offenses_faced():
    # STRONG normally scores 30 WR points, WEAK 10. SOFT gives up 50% more to both.
    # ONLYSTRONG allows 30 (a big raw number) but only ever faced STRONG: it's average.
    schedule = schedule_of((1, "STRONG", "D1"), (1, "WEAK", "D2"), (2, "STRONG", "D2"), (2, "WEAK", "D1"),
                           (3, "STRONG", "SOFT"), (4, "WEAK", "SOFT"), (5, "STRONG", "ONLYSTRONG"))
    strong = PlayerSeason(1, "S", "WR", "STRONG", SEASON, (
        line(1, "D1", 30), line(2, "D2", 30), line(3, "SOFT", 45), line(5, "ONLYSTRONG", 30)))
    weak = PlayerSeason(2, "W", "WR", "WEAK", SEASON, (line(1, "D2", 10), line(2, "D1", 10), line(4, "SOFT", 15)))
    strength = defense_strength([strong, weak], schedule)

    assert strength.allowed_per_game[("SOFT", "WR")] == strength.allowed_per_game[("ONLYSTRONG", "WR")] == 30.0
    # Same raw points allowed, but only SOFT beat its opponents' norms (shrunk: 2 games).
    assert strength.factor("SOFT", "WR") > 1.1
    assert strength.factor("ONLYSTRONG", "WR") == pytest.approx(1.0, abs=0.05)
    assert strength.rank("SOFT", "WR") == 1
    assert strength.factor("SOFT", "RB") == 1.0 and strength.rank("SOFT", "RB") is None   # no data -> neutral
    assert strength.schedule_factor(["SOFT", "D1"], "WR") == pytest.approx(
        (strength.factor("SOFT", "WR") + strength.factor("D1", "WR")) / 2, abs=1e-3)
    assert strength.schedule_factor([], "WR") == 1.0


def test_defense_factors_are_shrunk_and_capped():
    schedule = schedule_of((1, "O", "SIEVE"), (2, "O", "D2"), (3, "O", "D3"))
    player = PlayerSeason(1, "P", "TE", "O", SEASON, (line(1, "SIEVE", 60), line(2, "D2", 5), line(3, "D3", 5)))
    strength = defense_strength([player], schedule)
    assert strength.factor("SIEVE", "TE") <= 1.35            # one blowup game can't go extreme
    assert strength.factor("D2", "TE") >= 0.7


def test_no_schedule_is_neutral():
    player = PlayerSeason(1, "P", "WR", "O", SEASON, (line(1, "D", 30),))
    assert defense_strength([player], Schedule(games=())).factor("D", "WR") == 1.0


def test_game_implied_points_and_schedule_helpers():
    game = Game(week=7, home_team="DET", away_team="GB", home_score=None, away_score=None,
                spread_line=3.0, total_line=53.5)
    assert (game.implied_points("DET"), game.implied_points("GB")) == (28.25, 25.25)
    schedule = Schedule(games=(game,), opponents={("DET", 7): "GB", ("GB", 7): "DET"})
    assert schedule.current_week() == 7 and schedule.game("GB", 7) is game and schedule.game("GB", 8) is None
    assert schedule.points_per_game("DET") is None             # no finished games


# ---- Rest-of-season valuation -------------------------------------------------------------

def test_value_players_applies_schedule_factors():
    league = LeagueSettings(teams=1)
    players = [PlayerSeason(i, f"WR{i}", "WR", "T", SEASON, tuple(line(w, "D", 15.0) for w in range(1, 7)))
               for i in range(1, 6)]
    values, _ = value_players(players, [], league, 10, {1: 1.2, 2: 0.8})
    by_id = {v.player_id: v for v in values}
    assert by_id[1].ros_ppg == pytest.approx(by_id[1].base_ros_ppg * 1.2, abs=0.01)
    assert by_id[2].ros_ppg < by_id[3].ros_ppg < by_id[1].ros_ppg
    assert (by_id[1].schedule_factor, by_id[3].schedule_factor) == (1.2, 1.0)
    assert values[0].player_id == 1                            # easiest schedule ranks first


# ---- Weekly model --------------------------------------------------------------------------

def test_points_from_stats_matches_scoring_rules():
    stats = {"pass_yds": 250, "pass_tds": 2, "ints": 1, "rush_yds": 20, "rec": 5, "rec_yds": 50,
             "scrimmage_tds": 0.5, "fumbles_lost": 0.1, "two_pt": 0.1}
    assert points_from_stats(stats, "QB", PPR) == pytest.approx(10 + 8 - 2 + 2 + 5 + 5 + 3 - 0.2 + 0.2)
    assert points_from_stats({"rec": 4}, "TE", scoring_for("half", te_premium=0.5)) == 4.0


def test_model_stats_scales_to_rate_and_matchup():
    player = PlayerSeason(1, "P", "WR", "T", SEASON, (line(1, "D", 20, receptions=6, receiving_yards=80,
                                                          receiving_tds=1), line(2, "D", 8, receptions=4, receiving_yards=40)))
    easy = Matchup("OPP", True, sos_factor=1.2, sos_rank=1, implied_total=27.5, team_ppg=22.0, game_final=False)
    stats = model_stats(player, 15.0, easy, PPR)
    from services.weekly_model import IMPLIED_TOTAL_WEIGHT, SOS_WEIGHT
    expected = 15.0 * 1.2 ** SOS_WEIGHT * (27.5 / 22.0) ** IMPLIED_TOTAL_WEIGHT
    assert points_from_stats(stats, "WR", PPR) == pytest.approx(expected, abs=0.05)
    capped = Matchup("OPP", True, 1.0, None, implied_total=60.0, team_ppg=20.0, game_final=False)
    assert capped.implied_factor == 1.25


def test_props_line_needs_the_primary_market_and_replaces_model_tds():
    model = {"rec": 5, "rec_yds": 60, "rec_tds": 0.5, "rush_yds": 3}
    assert props_line({"scrimmage_tds": 0.4}, "WR", model) is None
    filled = props_line({"rec_yds": 75, "scrimmage_tds": 0.4}, "WR", model)
    assert filled == {"rec": 5, "rec_yds": 75, "rush_yds": 3, "scrimmage_tds": 0.4}


def test_rank_to_points_interpolates_and_clamps():
    curve = [20.0, 16.0, 12.0]
    assert _rank_to_points(curve, 1) == 20.0
    assert _rank_to_points(curve, 1.5) == 18.0
    assert _rank_to_points(curve, 10) == 12.0 and _rank_to_points(curve, 0) == 20.0


# ---- Week X rankings (seeded league) -------------------------------------------------------

def _player_id(session: Session, name: str) -> int:
    return session.query(Player.id).filter(Player.full_name == name).scalar()


def seed_week(session: Session) -> dict[str, int]:
    """Six finished weeks of AAA vs OPP, then week 7: AAA hosts BBB; the BYE team is idle."""
    for week in range(1, 7):
        session.add(NflGame(game_id=f"g{week}", season=SEASON, week=week, game_type="REG", home_team="AAA",
                            away_team="OPP", home_score=24, away_score=20, spread_line=3.0, total_line=44.0))
    session.add(NflGame(game_id="g7", season=SEASON, week=WEEK, game_type="REG", home_team="AAA",
                        away_team="BBB", spread_line=4.0, total_line=50.0))
    ids = {"wr1": _player_id(session, "WR Player 01"), "wr10": _player_id(session, "WR Player 10"),
           "wr20": _player_id(session, "WR Player 20"), "rb1": _player_id(session, "RB Player 01")}
    session.query(Player).filter(Player.id == ids["wr20"]).update({"nfl_team": "BYE"})

    def add(source, player_id, name, position="WR", team="AAA", **fields):
        session.add(WeeklySourceProjection(season=SEASON, week=WEEK, source=source, player_id=player_id,
                                           player_name=name, position=position, team=team, **fields))

    # Josh & Hayden love WR10; props and ESPN cover WR1; WR20's team is on bye.
    for expert, rank in (("7666", 1.0), ("1408", 2.0), ("consensus", 3.0)):
        add("yahoo_experts", ids["wr10"], "WR Player 10", scoring="ppr", expert=expert, rank=rank)
    add("yahoo_experts", ids["wr10"], "WR Player 10", scoring="half", expert="7666", rank=40.0)
    add("vegas_props", ids["wr1"], "WR Player 01", stats=json.dumps({"rec": 7.0, "rec_yds": 95.0,
                                                                      "scrimmage_tds": 0.6}))
    add("espn", ids["wr1"], "WR Player 01", stats=json.dumps({"rec": 6.0, "rec_yds": 80.0, "rec_tds": 0.5}))
    add("espn", ids["rb1"], "RB Player 01", position="RB", stats=json.dumps({"rush_yds": 80.0, "rush_tds": 0.7}))
    add("yahoo_experts", ids["wr20"], "WR Player 20", team="BYE", scoring="ppr", expert="consensus", rank=30.0)
    session.commit()
    return ids


def test_weekly_rankings_blend_sources(session, seeded):
    ids = seed_week(session)
    rankings = build_weekly_rankings(session, LeagueSettings())
    assert (rankings.season, rankings.week, rankings.scoring) == (SEASON, WEEK, "ppr")
    by_id = {p.player_id: p for p in rankings.players}

    wr1 = by_id[ids["wr1"]]
    assert set(wr1.sources) == {"vegas_props", "espn", "model"}
    props_points = points_from_stats({"rec": 7.0, "rec_yds": 95.0, "scrimmage_tds": 0.6}, "WR", PPR)
    assert wr1.sources["vegas_props"].points == pytest.approx(props_points, abs=0.01)
    blend = sum(WEIGHTS[k] * v.points for k, v in wr1.sources.items()) / sum(WEIGHTS[k] for k in wr1.sources)
    assert wr1.projection == pytest.approx(blend * wr1.matchup.sos_factor ** 0.25, abs=0.01)
    assert (wr1.matchup.opponent, wr1.matchup.home, wr1.matchup.implied_total) == ("BBB", True, 27.0)

    wr10 = by_id[ids["wr10"]]
    assert wr10.sources["josh_hayden"].rank == 1.5          # Hayden 1, Josh 2
    assert wr10.expert_ranks == {"Hayden Winks": 1.0, "Josh Norris": 2.0, "Yahoo consensus": 3.0}
    assert wr10.sources["josh_hayden"].points > wr10.sources["model"].points
    assert wr10.projection > wr10.sources["model"].points   # experts lifted him

    assert ids["wr20"] not in by_id                         # bye week
    assert _player_id(session, "WR Player 12") not in by_id  # no outside source -> likely inactive
    assert [p.rank for p in rankings.players] == list(range(1, len(rankings.players) + 1))
    assert rankings.source_counts["vegas_props"] == 1


def test_weekly_rankings_use_the_league_scoring_for_expert_ranks(session, seeded):
    ids = seed_week(session)
    half = {p.player_id: p for p in build_weekly_rankings(session, LeagueSettings(scoring="half")).players}
    assert half[ids["wr10"]].sources["josh_hayden"].rank == 40.0


def test_weekly_rankings_without_outside_sources_fall_back_to_the_model(session, seeded):
    session.add(NflGame(game_id="g7", season=SEASON, week=WEEK, game_type="REG", home_team="AAA",
                        away_team="BBB"))
    session.commit()
    rankings = build_weekly_rankings(session, LeagueSettings(), week=WEEK)
    assert rankings.players and all(set(p.sources) == {"model"} for p in rankings.players)


# ---- API -----------------------------------------------------------------------------------

@pytest.fixture
def week_client(client, engine):
    with Session(engine) as db_session:
        seed_week(db_session)
    valuation_module._cache.clear()
    return client


def test_weekly_rankings_endpoint(week_client):
    body = week_client.get("/api/rankings/weekly", params={"position": "WR", "limit": 5}).json()
    assert (body["season"], body["week"], body["scoring"], body["refresh"]) == (SEASON, WEEK, "ppr", None)
    assert [w["key"] for w in body["weights"]] == list(WEIGHTS)
    assert body["weights"][0] == {"key": "vegas_props", "label": "Vegas player props", "weight": 0.35, "players": 1}
    assert [r["rank"] for r in body["results"]] == [1, 2]   # only WRs some outside source covers
    first = body["results"][0]
    assert {"projection", "matchup", "sources", "expert_ranks", "pos_rank"} <= set(first)
    assert first["matchup"]["opponent"] == "BBB"
    assert week_client.get("/api/rankings/weekly", params={"week": 19}).status_code == 422


def test_weekly_rankings_endpoint_refreshes_when_enabled(week_client, monkeypatch):
    import api.weekly_routes as routes

    calls = []
    monkeypatch.setenv("WEEKLY_AUTO_REFRESH", "true")
    from config import get_settings
    get_settings.cache_clear()
    monkeypatch.setattr(routes, "refresh_sources", lambda session, season, week, **kw: calls.append(week) or
                        {"espn": {"state": "fresh", "rows": None, "message": None}})
    body = week_client.get("/api/rankings/weekly").json()
    assert calls == [WEEK] and body["refresh"]["espn"]["state"] == "fresh"
    week_client.get("/api/rankings/weekly", params={"refresh": "false"})
    assert calls == [WEEK]

    forced = []
    monkeypatch.setattr(routes, "refresh_sources", lambda session, season, week, force=False: forced.append(force) or {})
    assert week_client.post("/api/rankings/weekly/refresh", params={"week": 8, "force": "true"}).json() == {
        "season": SEASON, "week": 8, "sources": {}}
    assert forced == [True]


def test_schedule_strength_endpoint(week_client):
    body = week_client.get("/api/schedule-strength", params={"position": "WR"}).json()
    assert (body["season"], body["week"], body["position"]) == (SEASON, WEEK, "WR")
    (opp,) = body["defenses"]
    assert (opp["team"], opp["rank"], opp["games"], opp["this_week_opponent"]) == ("OPP", 1, 6, None)
    aaa = next(o for o in body["offenses"] if o["team"] == "AAA")
    assert aaa["this_week"] == "BBB" and aaa["games_left"] == 1
    assert aaa["next_weeks"] == [{"opponent": "BBB", "sos_factor": 1.0}]


def test_rankings_include_schedule_factor(client):
    first = client.get("/api/rankings", params={"limit": 1}).json()["results"][0]
    assert first["schedule_factor"] == 1.0                   # seeded league has no schedule


def test_valuation_exposes_schedule(session, seeded):
    seed_week(session)
    valuation = build_valuation(session, LeagueSettings())
    assert valuation.schedule.current_week() == WEEK
    assert valuation.defense.games[("OPP", "WR")] == 6


def test_agent_tools_for_week_and_schedule(session, seeded):
    from tools.definitions import TOOLS

    seed_week(session)
    weekly = TOOLS.call(session, "get_weekly_rankings", {"position": "WR", "limit": 1})
    assert weekly["week"] == WEEK and weekly["refresh"] is None and len(weekly["results"]) == 1
    strength = TOOLS.call(session, "get_schedule_strength", {"position": "WR"})
    assert strength["defenses"][0]["team"] == "OPP"


# ---- Review regressions ----------------------------------------------------------------------

def test_props_rows_alone_do_not_mark_everyone_else_inactive(session, seeded):
    # Only Thursday-style props are stored (Yahoo/ESPN not published): nobody else is dropped.
    session.add(NflGame(game_id="g7", season=SEASON, week=WEEK, game_type="REG", home_team="AAA", away_team="BBB"))
    wr1 = _player_id(session, "WR Player 01")
    session.add(WeeklySourceProjection(season=SEASON, week=WEEK, source="vegas_props", player_id=wr1,
                                       player_name="WR Player 01", position="WR", team="AAA",
                                       stats=json.dumps({"scrimmage_tds": 0.5})))   # anytime-TD only
    session.commit()
    rankings = build_weekly_rankings(session, LeagueSettings(), week=WEEK)
    by_id = {p.player_id: p for p in rankings.players}
    assert _player_id(session, "WR Player 30") in by_id                  # model-only players stay
    assert set(by_id[wr1].sources) == {"model"}                          # TD-only props: kept, model used


def test_team_comes_from_any_source_that_knows_it(session, seeded):
    seed_week(session)
    rb2 = _player_id(session, "RB Player 02")
    session.query(Player).filter(Player.id == rb2).update({"nfl_team": None})
    for source, team, extra in (("espn", None, {"stats": json.dumps({"rush_yds": 60.0})}),
                                ("yahoo_experts", "AAA", {"scoring": "ppr", "expert": "consensus", "rank": 2.0})):
        session.add(WeeklySourceProjection(season=SEASON, week=WEEK, source=source, player_id=rb2,
                                           player_name="RB Player 02", position="RB", team=team, **extra))
    session.commit()
    by_id = {p.player_id: p for p in build_weekly_rankings(session, LeagueSettings()).players}
    assert by_id[rb2].team == "AAA" and by_id[rb2].matchup.opponent == "BBB"


def test_rank_curves_ignore_players_left_out(session, seeded):
    ids = seed_week(session)
    rankings = build_weekly_rankings(session, LeagueSettings())
    wr10 = next(p for p in rankings.players if p.player_id == ids["wr10"])
    # Only WR1 (props/ESPN/model) and WR10 are active, so a rank-1.5 lands between their anchors,
    # not on some inactive model-only WR's projection.
    wr1 = next(p for p in rankings.players if p.player_id == ids["wr1"])
    anchors = sorted(
        [sum(WEIGHTS[k] * v.points for k, v in p.sources.items() if k in ("vegas_props", "espn", "model"))
         / sum(WEIGHTS[k] for k in p.sources if k in ("vegas_props", "espn", "model")) for p in (wr1, wr10)],
        reverse=True)
    assert wr10.sources["josh_hayden"].points == pytest.approx((anchors[0] + anchors[1]) / 2, abs=0.01)


def test_current_week_ignores_stale_unplayed_games():
    from datetime import date

    stale = Game(week=5, home_team="A", away_team="B", home_score=None, away_score=None,
                 spread_line=None, total_line=None, gameday="2026-10-04")
    upcoming = Game(week=7, home_team="C", away_team="D", home_score=None, away_score=None,
                    spread_line=None, total_line=None, gameday="2026-10-25")
    schedule = Schedule(games=(stale, upcoming))
    assert schedule.current_week(today=date(2026, 10, 23)) == 7
    assert schedule.current_week(today=date(2026, 10, 5)) == 5


def test_weekly_endpoint_only_auto_refreshes_the_current_week_same_site(week_client, monkeypatch):
    import api.weekly_routes as routes
    from config import get_settings

    calls = []
    monkeypatch.setenv("WEEKLY_AUTO_REFRESH", "true")
    get_settings.cache_clear()
    monkeypatch.setattr(routes, "refresh_sources", lambda session, season, week, **kw: calls.append(week) or {})
    week_client.get("/api/rankings/weekly", params={"week": 9})                       # not the current week
    week_client.get("/api/rankings/weekly", headers={"Sec-Fetch-Site": "cross-site"})  # another website
    assert calls == []
    week_client.get("/api/rankings/weekly", headers={"Sec-Fetch-Site": "same-site"})
    assert calls == [WEEK]
