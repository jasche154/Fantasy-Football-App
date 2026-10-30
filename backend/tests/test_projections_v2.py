"""Round 8: team profiles, model factors, weather, kicker / defense models, matchup simulation."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import numpy as np
import polars as pl
import pytest

from database import advanced_stats
from database.models import GameForecast, NflGame, TeamGameStats
from integrations import yahoo
from services import game_features as gf
from services import kdst_model as km
from services import matchup as mu
from services import projection_factors as pf
from services import weather
from services.league import LeagueSettings


def _team_game(team, opponent, week=1, **overrides):
    values = dict(season=2026, week=week, team=team, opponent=opponent, points_for=24, points_against=20, plays=62,
                  dropbacks=38, pass_epa=3.0, rush_epa=-1.0, sacks_taken=2.0, ints_thrown=1, fumbles_lost=0,
                  def_sacks=3.0, def_ints=1, def_fumble_rec=1, def_tds=0, def_safeties=0, def_blocks=0,
                  rz_trips=3, rz_tds=2, fourth_downs=4, fourth_down_goes=1, fg_att=2, fg_made=2, pat_att=3, pat_made=3)
    values.update(overrides)
    return TeamGameStats(**values)


# ---------- team profiles and conditions ----------

def test_profiles_shrink_toward_the_league_and_mirror_defense_from_opponents():
    rows = [_team_game("AAA", "BBB", w, sacks_taken=5.0) for w in (1, 2)] + \
           [_team_game("BBB", "AAA", w, def_sacks=5.0) for w in (1, 2)]
    profiles = gf.build_profiles(rows)
    assert profiles.league is gf.LEAGUE_DEFAULT                       # under 32 team-games: long-run averages
    aaa, bbb = profiles.get("AAA"), profiles.get("BBB")
    raw = 5.0 / 38
    assert gf.LEAGUE_DEFAULT.sacks_taken_per_db < aaa.sacks_taken_per_db < raw       # shrunk, not raw
    assert bbb.sack_rate == pytest.approx(aaa.sacks_taken_per_db, rel=0.01)           # what BBB made = what AAA took
    assert profiles.get("ZZZ") is profiles.league


def test_conditions_use_records_then_forecasts_and_ignore_domes():
    dome = NflGame(game_id="g1", roof="dome", wind=20.0, temp=10.0)
    played = NflGame(game_id="g2", roof="outdoors", wind=22.0, temp=25.0)
    upcoming = NflGame(game_id="g3", roof="outdoors")
    forecast = {"g3": gf.Conditions(False, 30.0, 18.0)}
    assert gf.conditions_for(dome).indoors and gf.conditions_for(dome).wind_over == 0
    assert gf.conditions_for(played).wind_over == 12 and gf.conditions_for(played).cold == 15
    assert gf.conditions_for(upcoming, forecast).wind_over == 8
    assert gf.conditions_for(upcoming) == gf.UNKNOWN and gf.conditions_for(None) == gf.UNKNOWN


# ---------- projection factors ----------

def test_factor_applies_coefficients_and_stays_within_limits(monkeypatch):
    monkeypatch.setattr(pf, "coefficients", lambda: {"WR": [-0.1, 0, 0, 0, -0.1, 0, 0.04]})
    calm = pf.FactorInputs()
    windy = pf.FactorInputs(wind=20)
    assert pf.factor("WR", calm) == pytest.approx(0.9)
    assert pf.factor("WR", windy) == pytest.approx(0.7 if pf.FACTOR_LIMITS[0] <= 0.7 else pf.FACTOR_LIMITS[0])
    assert pf.factor("WR", pf.FactorInputs(wind=200)) == pf.FACTOR_LIMITS[0]
    assert pf.factor("QB", calm) == 1.0                                # no coefficients: unchanged
    assert "wind" in pf.describe("WR", windy) and "calibration" in pf.describe("WR", calm)


def test_fit_recovers_a_known_effect_and_reports_held_out_seasons():
    rng = np.random.default_rng(1)
    rows = []
    for season in (2023, 2024, 2025):
        for _ in range(400):
            wind = float(rng.uniform(0, 20))
            if_active = float(rng.uniform(5, 20))
            actual = if_active * (1 - 0.1 * wind / 10) + float(rng.normal(0, 1))
            rows.append({"season": season, "week": 5, "pos": "QB", "played": True, "if_active": if_active,
                         "projected": if_active, "actual": actual,
                         "x": pf.FactorInputs(wind=wind).vector()})
    beta = pf.fit(rows)["QB"]
    assert beta[pf.FEATURES.index("wind")] == pytest.approx(-0.1, abs=0.03)
    report = pf.leave_season_out(rows, [2025])
    assert report["2025"]["after"]["mae"] < report["2025"]["before"]["mae"]


def test_model_settings_can_turn_factors_off():
    from services.weekly_model import WeeklyModelSettings

    assert "factors on" in WeeklyModelSettings().describe()
    assert "factors off" in WeeklyModelSettings(use_factors=False).describe()


# ---------- weather ----------

def _game(**kw):
    values = dict(game_id="2026_08_ATL_TB", season=2026, week=8, game_type="REG", home_team="TB", away_team="ATL",
                  gameday=date.today().isoformat(), gametime="13:00", roof="outdoors", stadium_id="TAM00")
    values.update(kw)
    return NflGame(**values)


def test_due_games_skip_domes_played_games_far_dates_and_neutral_sites(session):
    today = date.today()
    session.add_all([
        _game(),
        _game(game_id="dome", home_team="DET", away_team="GB", roof="dome", stadium_id="DET00"),
        _game(game_id="played", home_team="BUF", away_team="MIA", home_score=20, away_score=10, stadium_id="BUF00"),
        _game(game_id="far", home_team="CHI", away_team="MIN", gameday=(today + timedelta(days=20)).isoformat(),
              stadium_id="CHI98"),
        _game(game_id="london", week=8, home_team="JAX", away_team="NE", stadium_id="LON00"),
        _game(game_id="jax_home", week=3, home_team="JAX", away_team="HOU", stadium_id="JAX00",
              gameday=(today - timedelta(days=30)).isoformat()),
        _game(game_id="jax_home2", week=5, home_team="JAX", away_team="TEN", stadium_id="JAX00",
              gameday=(today - timedelta(days=16)).isoformat()),
    ])
    session.commit()
    assert [g.game_id for g in weather.due_games(session, 2026, 8, today)] == ["2026_08_ATL_TB"]


def test_forecast_is_read_at_kickoff_hour_and_cached(session):
    session.add(_game())
    session.commit()
    calls = []

    def handler(request):
        calls.append(request)
        temps = [50.0 + h for h in range(24)]
        winds = [h * 1.0 for h in range(24)]
        return httpx.Response(200, json={"hourly": {"temperature_2m": temps, "wind_speed_10m": winds}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert weather.refresh_forecasts(session, http, 2026, 8) == 1
    row = session.get(GameForecast, "2026_08_ATL_TB")
    assert (row.temp, row.wind) == (63.0, 13.0)                       # the 13:00 hour
    assert weather.refresh_forecasts(session, http, 2026, 8) == 0     # fresh: not refetched
    assert len(calls) == 1
    assert weather.forecasts(session, ["2026_08_ATL_TB"])["2026_08_ATL_TB"].wind == 13.0


# ---------- kicker and defense ----------

def test_points_allowed_tiers_follow_yahoo_defaults():
    tiers = [km.pa_points(p, km.DEFAULT_DST) for p in (0, 3, 10, 17, 24, 31, 40)]
    assert tiers == [10, 7, 4, 1, 0, -1, -4]


COEFS = {"kicker": {"fga": [0.2, 0.08, -1.0, 1.0], "tds": [0.0, 0.12], "wind": {b: 0.05 for b in km.BANDS},
                    "cold": {b: 0.02 for b in km.BANDS}},
         "defense": {"sacks": [0.0, 1.0], "takeaways": [0.0, 1.0], "int_share": 0.6, "td_per_takeaway": 0.06,
                     "td_base": 0.03, "safety_rate": 0.02, "block_rate": 0.05, "pa_residuals": [-7.0, 0.0, 7.0]}}


def _inputs(roof="outdoors", wind=None):
    profiles = gf.TeamProfiles(league=gf.LEAGUE_DEFAULT)
    game = NflGame(game_id="g", home_team="AAA", away_team="BBB", spread_line=3.0, total_line=44.0, roof=roof,
                   wind=wind, temp=60.0 if wind is not None else None)
    return km.WeekInputs(season=2026, week=8, profiles=profiles, games={"AAA": game, "BBB": game}, kickers={},
                         league_bands={"0_39": 0.95, "40_49": 0.85, "50": 0.7},
                         league_mix={"0_39": 0.5, "40_49": 0.3, "50": 0.2})


def test_kicker_projection_rises_with_the_implied_total_and_falls_in_wind(monkeypatch):
    monkeypatch.setattr(km, "coefficients", lambda: COEFS)
    calm, windy = km.project_kicker(_inputs(), "AAA"), km.project_kicker(_inputs(wind=25.0), "AAA")
    underdog = km.project_kicker(_inputs(), "BBB")
    assert calm.points > underdog.points                              # 23.5 vs 20.5 implied
    assert windy.points < calm.points and windy.accuracy["50"] < calm.accuracy["50"]
    assert calm.fg_attempts == pytest.approx(0.2 + 0.08 * 23.5)
    assert km.project_kicker(_inputs(), "ZZZ") is None                 # no game


def test_defense_projection_scores_sacks_takeaways_and_points_allowed(monkeypatch):
    monkeypatch.setattr(km, "coefficients", lambda: COEFS)
    proj = km.project_defense(_inputs(), "AAA")
    league = gf.LEAGUE_DEFAULT
    assert proj.points_allowed == pytest.approx(20.5)                 # BBB's implied total
    assert proj.points_allowed_score == pytest.approx(np.mean([km.pa_points(p, km.DEFAULT_DST) for p in (13.5, 20.5, 27.5)]), abs=0.01)
    assert proj.sacks == pytest.approx(league.dropbacks_pg * league.sack_rate, rel=0.01)
    custom = {**km.DEFAULT_DST, "sack": 2.0}
    assert km.project_defense(_inputs(), "AAA", custom).points == pytest.approx(proj.points + proj.sacks, rel=0.01)


def test_yahoo_kicker_and_defense_modifiers_override_defaults():
    content = {"league": [{}, {"settings": [{"roster_positions": [{"roster_position": {"position": "K", "count": 1}},
                                                                   {"roster_position": {"position": "DEF", "count": 1}}],
                                             "stat_modifiers": {"stats": [{"stat": {"stat_id": 23, "value": "6"}},
                                                                          {"stat": {"stat_id": 32, "value": "1.5"}},
                                                                          {"stat": {"stat_id": 33, "value": "999"}}]}}]}]}
    rules = yahoo.parse_settings(content)
    assert rules["starters"] == {"K": 1, "DST": 1}
    assert rules["k_scoring"]["50"] == 6.0 and rules["dst_scoring"]["sack"] == 1.5
    assert rules["dst_scoring"]["int"] == km.DEFAULT_DST["int"]        # 999 is corrupt: ignored


def test_kdst_endpoint_lists_kickers_and_defenses(client):
    for position in ("K", "DST"):
        body = client.get("/api/rankings/weekly/kdst", params={"position": position}).json()
        points = [r["projection"] for r in body["results"]]
        assert points == sorted(points, reverse=True)
    assert client.get("/api/rankings/weekly/kdst", params={"position": "P"}).status_code == 422


# ---------- play-by-play aggregation ----------

def test_pbp_counts_red_zone_trips_fourth_downs_and_player_usage():
    pbp = pl.DataFrame({
        "season_type": ["REG"] * 6, "week": [1] * 6, "posteam": ["AAA"] * 6,
        "play_type": ["pass", "run", "run", "field_goal", "run", "punt"],
        "qb_dropback": [1, 0, 0, 0, 0, 0], "drive_inside20": [1, 1, 0, 0, 1, 0],
        "fixed_drive": [1, 1, 2, 2, 3, 4], "fixed_drive_result": ["Touchdown", "Touchdown", "Punt", "Field goal",
                                                                "Field goal", "Punt"],
        "down": [1, 2, 4, 4, 4, 4], "yardline_100": [8, 3, 60, 30, 15, 70],
        "rusher_player_id": [None, "R1", "R1", None, "R1", None], "receiver_player_id": ["W1", None, None, None, None, None],
    })
    counts = advanced_stats.pbp_team_counts(pbp)[(1, "AAA")]
    assert counts["plays"] == 4 and counts["dropbacks"] == 1
    assert (counts["rz_trips"], counts["rz_tds"]) == (2, 1)
    assert (counts["fourth_downs"], counts["fourth_down_goes"]) == (4, 2)
    usage = {r["gsis"]: r for r in advanced_stats.pbp_red_zone(pbp)}
    assert (usage["R1"]["rz_carries"], usage["R1"]["i10_carries"]) == (2, 1)
    assert (usage["W1"]["rz_targets"], usage["W1"]["i10_targets"]) == (1, 1)


# ---------- matchup ----------

def _wp(pid, proj, floor, ceiling, position="RB", actual=None, play_prob=1.0):
    from services.weekly_model import Matchup
    from services.weekly_rankings import WeeklyPlayer

    return WeeklyPlayer(player_id=pid, name=f"P{pid}", position=position, team="AAA",
                        matchup=Matchup("BBB", True, 1.0, None, None, None, False), projection=proj, sources={},
                        expert_ranks={}, if_active=proj, floor=floor, ceiling=ceiling, actual=actual, play_prob=play_prob)


def test_simulation_respects_actual_points_and_sitting_players():
    rng = np.random.default_rng(0)
    done = mu.simulate_totals([_wp(1, 10, 5, 15, actual=22.0)], rng, 1000)
    assert np.all(done == 22.0)
    out = mu.simulate_totals([_wp(2, 10, 5, 15, play_prob=0.0)], rng, 1000)
    assert np.all(out == 0.0)
    assert mu.win_probability([_wp(3, 20, 15, 25)], [_wp(4, 10, 5, 15)]) > 0.9


def test_upside_and_safe_keys_pick_different_players_for_one_slot():
    from services import my_team as mt

    steady, boom = _wp(1, 12, 10, 14), _wp(2, 11.5, 2, 26)
    settings = LeagueSettings(starters={"RB": 1})
    pick = lambda mode: mt.best_lineup([steady, boom], settings, key=mu._score(mode))["RB"]  # noqa: E731
    assert pick("expected") == [1] and pick("safe") == [1] and pick("upside") == [2]


def test_scoreboard_parser_reads_points_projection_and_win_probability():
    team = lambda key, name, pts, proj, wp: {"team": [[{"team_key": key}, {"name": name}],  # noqa: E731
                                                      {"win_probability": wp, "team_points": {"total": pts},
                                                       "team_projected_points": {"total": proj}}]}
    content = {"league": [{}, {"scoreboard": {"0": {"matchups": {"0": {"matchup": {
        "status": "midevent", "0": {"teams": {"0": team("t1", "Mine", "8.6", "102.6", 0.32),
                                              "1": team("t6", "Theirs", "12", "107", 0.68), "count": 2}}}},
        "count": 1}}}}]}
    (matchup,) = yahoo.parse_scoreboard(content)
    assert matchup["status"] == "midevent"
    assert matchup["teams"][0] == {"team_key": "t1", "name": "Mine", "points": 8.6, "projected": 102.6,
                                   "win_probability": 0.32}
    assert yahoo.parse_scoreboard({}) == []
