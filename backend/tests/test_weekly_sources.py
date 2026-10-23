"""Outside weekly sources: props math, The Odds API, Yahoo experts, ESPN, and the refresher."""
import json
import math
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from sqlalchemy.orm import Session

from config import Settings
from database.models import ExternalApiCall, NflGame, WeeklySourceProjection
from integrations import espn, odds_api, yahoo_experts
from integrations.weekly_sources import PlayerMatcher, SourceError, normalize_name, normalize_team
from services import props_math, weekly_refresh

SEASON, WEEK = 2026, 7


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def rows_for(session: Session, source: str) -> list[WeeklySourceProjection]:
    return session.query(WeeklySourceProjection).filter_by(source=source).all()


# ---- Props math --------------------------------------------------------------------------

def test_implied_and_fair_probability():
    assert props_math.implied_probability(-150) == pytest.approx(0.6)
    assert props_math.implied_probability(150) == pytest.approx(0.4)
    assert props_math.fair_probability(-110, -110) == pytest.approx(0.5)
    assert props_math.fair_probability(-150, 120) == pytest.approx(0.6 / (0.6 + 100 / 220))


def test_poisson_mean_round_trips_and_moves_with_price():
    mean = props_math.poisson_mean(4.5, 0.5)
    assert props_math.poisson_over(4.5, mean) == pytest.approx(0.5, abs=1e-4)
    assert 4.5 < mean < 5.5                              # a 50/50 at 4.5 means ~5 catches
    assert props_math.poisson_mean(4.5, 0.65) > mean     # juiced over -> higher mean
    assert props_math.poisson_mean(1.5, 0.999) < props_math.POISSON_SEARCH_MAX   # clamped, finite


def test_yardage_and_anytime_td_means():
    assert props_math.yardage_mean("rec_yds", 60.5, 0.5) == pytest.approx(60.5)
    assert props_math.yardage_mean("rec_yds", 60.5, 0.6) > 60.5
    assert props_math.yardage_mean("rush_yds", 10.0, 0.001) == 0.0                 # never negative
    assert props_math.anytime_td_mean(100, -100) == pytest.approx(math.log(2))   # 50% -> ln 2
    assert props_math.anytime_td_mean(100) < math.log(2)                           # margin removed


# ---- Matching ------------------------------------------------------------------------------

def test_normalize_name_and_team():
    assert normalize_name("Kenneth Walker III") == "kenneth walker"
    assert normalize_name("Ja'Marr Chase") == "jamarr chase"
    assert normalize_name("Amon-Ra St. Brown") == "amon ra st brown"
    assert normalize_name("José Nuñez Jr.") == "jose nunez"
    assert (normalize_team("LAR"), normalize_team("wsh"), normalize_team(None)) == ("LA", "WAS", None)


def test_player_matcher_prefers_ids_then_team(session, seeded):
    matcher = PlayerMatcher(session)
    assert matcher.match("Anyone", yahoo_id="555") == seeded["chase"]
    assert matcher.match("JaMarr Chase") == seeded["chase"]
    assert matcher.match("Mike Williams") is None                        # two of them
    assert matcher.match("Mike Williams", team="LAC") == seeded["mw2"]
    assert matcher.match("Mike Williams", teams=("NYJ", "BUF")) == seeded["mw1"]
    assert matcher.match("Ja'Marr Chase", team="BAL") == seeded["chase"]  # unique name, new team
    assert matcher.match("Nobody Here") is None


# ---- The Odds API ----------------------------------------------------------------------------

def _outcome(name, description, price, point=None):
    item = {"name": name, "description": description, "price": price}
    return item if point is None else {**item, "point": point}


def props_payload(player="Ja'Marr Chase"):
    book = {"key": "draftkings", "markets": [
        {"key": "player_reception_yds", "outcomes": [_outcome("Over", player, -115, 80.5),
                                                     _outcome("Under", player, -105, 80.5)]},
        {"key": "player_receptions", "outcomes": [_outcome("Over", player, 110, 6.5),
                                                  _outcome("Under", player, -140, 6.5)]},
        {"key": "player_anytime_td", "outcomes": [_outcome("Yes", player, 120)]},
        {"key": "h2h", "outcomes": [_outcome("Over", player, -110, 1)]},          # ignored market
    ]}
    other = {"key": "fanduel", "markets": [
        {"key": "player_reception_yds", "outcomes": [_outcome("Over", player, -110, 84.5),
                                                     _outcome("Under", player, -110, 84.5)]},
        {"key": "player_rush_yds", "outcomes": [_outcome("Over", player, -110, None)]},   # incomplete
    ]}
    return {"id": "evt1", "bookmakers": [book, other]}


def test_expected_stats_averages_books_and_skips_incomplete_markets():
    stats = odds_api.expected_stats(props_payload())["Ja'Marr Chase"]
    assert set(stats) == {"rec_yds", "rec", "scrimmage_tds"}
    assert 80.5 < stats["rec_yds"] < 84.5
    assert 5.0 < stats["rec"] < 7.0
    assert stats["scrimmage_tds"] == pytest.approx(props_math.anytime_td_mean(120))


def _add_games(session: Session, *games: tuple[str, str]) -> None:
    for home, away in games:
        session.add(NflGame(game_id=f"{SEASON}_{WEEK:02d}_{away}_{home}", season=SEASON, week=WEEK,
                            game_type="REG", home_team=home, away_team=away, spread_line=-2.5,
                            total_line=47.5))
    session.commit()


def odds_handler(calls: list, events: list[dict], remaining: str = "400"):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        headers = {"x-requests-remaining": remaining}
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events, headers=headers)
        return httpx.Response(200, json=props_payload(), headers=headers)
    return handler


def _event(event_id, home, away, hours_from_now=24):
    kickoff = (datetime.now(timezone.utc) + timedelta(hours=hours_from_now)).isoformat().replace("+00:00", "Z")
    return {"id": event_id, "home_team": home, "away_team": away, "commence_time": kickoff}


def test_odds_refresh_buys_each_game_once_inside_the_window(session, seeded):
    _add_games(session, ("CIN", "BAL"), ("KC", "BUF"), ("DAL", "NYG"))
    calls = []
    events = [_event("evt1", "Cincinnati Bengals", "Baltimore Ravens"),
              _event("old", "Kansas City Chiefs", "Buffalo Bills", hours_from_now=-1),   # kicked off
              _event("later", "Dallas Cowboys", "New York Giants", hours_from_now=72),   # not due yet
              _event("other", "Miami Dolphins", "New York Jets")]                         # not this week
    client = odds_api.OddsApiClient("key", mock_client(odds_handler(calls, events)))
    assert odds_api.refresh(session, client, SEASON, WEEK) == 1
    assert [c.url.path.rsplit("/", 2)[-2] for c in calls[1:]] == ["evt1"]               # one game bought
    assert calls[1].url.params["markets"] == ",".join(odds_api.MARKETS)
    (row,) = rows_for(session, odds_api.SOURCE)
    assert (row.player_id, row.team, row.opponent, row.position) == (seeded["chase"], "CIN", "BAL", "WR")
    assert json.loads(row.stats)["rec_yds"] > 80
    assert client.remaining == 400

    calls.clear()
    assert odds_api.refresh(session, client, SEASON, WEEK) == 0                          # already bought
    assert len(calls) == 1 and calls[0].url.path.endswith("/events")                     # only the free list
    assert odds_api.refresh(session, client, SEASON, WEEK, force=True) == 1               # explicit re-buy
    assert session.query(ExternalApiCall).filter_by(source="odds_api:event:evt1", ok=True).count() == 2


def test_odds_refresh_matches_neutral_site_games_either_way(session, seeded):
    _add_games(session, ("BAL", "CIN"))                       # nflverse lists BAL as home
    events = [_event("london", "Cincinnati Bengals", "Baltimore Ravens")]
    client = odds_api.OddsApiClient("key", mock_client(odds_handler([], events)))
    assert odds_api.refresh(session, client, SEASON, WEEK) == 1
    assert rows_for(session, odds_api.SOURCE)[0].opponent == "BAL"


def test_odds_refresh_retries_games_without_props_later(session, seeded, monkeypatch):
    _add_games(session, ("CIN", "BAL"))
    payloads = [{"bookmakers": []}, props_payload()]

    def handler(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=[_event("evt1", "Cincinnati Bengals", "Baltimore Ravens")])
        return httpx.Response(200, json=payloads.pop(0))

    client = odds_api.OddsApiClient("key", mock_client(handler))
    assert odds_api.refresh(session, client, SEASON, WEEK) == 1      # nothing posted yet: no rows
    assert odds_api.refresh(session, client, SEASON, WEEK) == 0      # too soon to retry
    monkeypatch.setattr(odds_api, "EMPTY_RETRY_HOURS", 0)
    assert odds_api.refresh(session, client, SEASON, WEEK) == 1
    assert len(rows_for(session, odds_api.SOURCE)) == 1


def test_odds_failure_partway_keeps_games_already_bought(session, seeded):
    _add_games(session, ("CIN", "BAL"), ("KC", "BUF"))
    events = [_event("evt1", "Cincinnati Bengals", "Baltimore Ravens"), _event("evt2", "Kansas City Chiefs", "Buffalo Bills")]

    def handler(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=events)
        if "evt2" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json=props_payload())

    client = odds_api.OddsApiClient("key", mock_client(handler))
    with pytest.raises(SourceError, match=r"HTTP 503\. \(1 games stored before it stopped\)"):
        odds_api.refresh(session, client, SEASON, WEEK)
    assert [r.team for r in rows_for(session, odds_api.SOURCE)] == ["CIN"]              # paid rows kept
    with pytest.raises(SourceError, match="retrying after"):
        odds_api.refresh(session, client, SEASON, WEEK)                                   # backs off


def test_odds_unexpected_payloads_become_source_errors(session, seeded):
    _add_games(session, ("CIN", "BAL"))
    bad_props = {"bookmakers": [{"markets": [{"key": "player_receptions", "outcomes": [
        {"name": "Over", "description": "Ja'Marr Chase", "point": 4.5}, {"name": "Under", "description": "Ja'Marr Chase"}]}]}]}

    def handler(request):
        if request.url.path.endswith("/events"):
            return httpx.Response(200, json=[_event("evt1", "Cincinnati Bengals", "Baltimore Ravens")],
                                  headers={"x-requests-remaining": "lots"})
        return httpx.Response(200, json=bad_props)

    with pytest.raises(SourceError, match="unexpected props"):
        odds_api.refresh(session, odds_api.OddsApiClient("key", mock_client(handler)), SEASON, WEEK)
    not_a_list = odds_api.OddsApiClient("key", mock_client(lambda r: httpx.Response(200, json={"message": "x"})))
    with pytest.raises(SourceError, match="unexpected events"):
        not_a_list.events()


def test_odds_refresh_stops_before_overdrawing_credits(session, seeded):
    _add_games(session, ("CIN", "BAL"))
    client = odds_api.OddsApiClient("key", mock_client(
        odds_handler([], [_event("evt1", "Cincinnati Bengals", "Baltimore Ravens")], remaining="12")))
    with pytest.raises(SourceError, match=r"credits are nearly used up \(12 left\)"):
        odds_api.refresh(session, client, SEASON, WEEK)
    assert rows_for(session, odds_api.SOURCE) == []


def test_odds_client_errors(session, seeded):
    with pytest.raises(SourceError, match="ODDS_API_KEY"):
        odds_api.OddsApiClient("", mock_client(lambda r: httpx.Response(200)))
    _add_games(session, ("CIN", "BAL"))
    bad = odds_api.OddsApiClient("key", mock_client(lambda r: httpx.Response(401)))
    with pytest.raises(SourceError, match="rejected the key"):
        odds_api.refresh(session, bad, SEASON, WEEK)
    assert session.query(ExternalApiCall).filter_by(source="odds_api", ok=False).count() == 1


def test_odds_refresh_needs_a_schedule(session, seeded):
    client = odds_api.OddsApiClient("key", mock_client(lambda r: httpx.Response(200, json=[])))
    with pytest.raises(SourceError, match="populate_stats"):
        odds_api.refresh(session, client, SEASON, WEEK)


# ---- Yahoo experts --------------------------------------------------------------------------

def yahoo_payload(position):
    if position != "WR":
        return {"players": []}
    return {"players": [
        {"name": "Ja'Marr Chase", "position": "WR", "team": "CIN", "playerYahooId": "555", "rank": 2,
         "opponent": "vs. BAL", "experts": {"7666": "1", "1408": "3", "93": "2", "285": ""}},
        {"name": "Unknown Rookie", "position": "WR", "team": "NO", "playerYahooId": "9", "rank": 50,
         "opponent": "at ATL", "experts": {}},
        {"name": "Some Kicker", "position": "K", "team": "MIN", "rank": 1, "experts": {}},
    ]}


def test_yahoo_rows_keep_consensus_and_tracked_experts(session, seeded):
    rows = yahoo_experts.ranking_rows(yahoo_payload("WR"), SEASON, WEEK, "ppr", PlayerMatcher(session))
    chase = {r["expert"]: r["rank"] for r in rows if r["player_id"] == seeded["chase"]}
    assert chase == {"consensus": 2.0, "7666": 1.0, "1408": 3.0}       # untracked + blank skipped
    rookie = next(r for r in rows if r["player_name"] == "Unknown Rookie")
    assert (rookie["player_id"], rookie["opponent"]) == (None, "ATL")
    assert all(r["position"] in ("WR",) for r in rows)


def test_yahoo_refresh_fetches_every_position_and_scoring(session, seeded):
    seen = []

    def handler(request):
        seen.append((request.url.params["position"], request.url.params["scoring"]))
        return httpx.Response(200, json=yahoo_payload(request.url.params["position"]))

    assert yahoo_experts.refresh(session, mock_client(handler), SEASON, WEEK) == 12   # 4 rows x 3 formats
    assert len(seen) == 12 and ("TE", "HALF") in seen
    assert {r.scoring for r in rows_for(session, yahoo_experts.SOURCE)} == {"ppr", "half", "std"}


def test_yahoo_refresh_is_all_or_nothing_across_scoring_formats(session, seeded):
    def handler(request):
        if request.url.params["scoring"] == "STD":
            return httpx.Response(500)
        return httpx.Response(200, json=yahoo_payload(request.url.params["position"]))

    with pytest.raises(SourceError, match="HTTP 500"):
        yahoo_experts.refresh(session, mock_client(handler), SEASON, WEEK)
    assert rows_for(session, yahoo_experts.SOURCE) == []      # PPR/HALF weren't stored on their own


def test_yahoo_numeric_ids_still_match(session, seeded):
    payload = {"players": [{"name": "Somebody Else", "position": "WR", "team": "CIN", "playerYahooId": 555,
                            "rank": 1, "experts": {}}]}
    (row,) = yahoo_experts.ranking_rows(payload, SEASON, WEEK, "ppr", PlayerMatcher(session))
    assert row["player_id"] == seeded["chase"]


def test_yahoo_errors_keep_previous_snapshot(session, seeded):
    yahoo_experts.refresh(session, mock_client(lambda r: httpx.Response(
        200, json=yahoo_payload(r.url.params["position"]))), SEASON, WEEK)
    with pytest.raises(SourceError, match="HTTP 500"):
        yahoo_experts.refresh(session, mock_client(lambda r: httpx.Response(500)), SEASON, WEEK)
    with pytest.raises(SourceError, match="non-JSON"):
        yahoo_experts.refresh(session, mock_client(lambda r: httpx.Response(200, text="<html>")), SEASON, WEEK)
    with pytest.raises(SourceError, match="unexpected response"):
        yahoo_experts.refresh(session, mock_client(lambda r: httpx.Response(200, json=[1, 2])), SEASON, WEEK)
    assert len(rows_for(session, yahoo_experts.SOURCE)) == 12


# ---- ESPN -----------------------------------------------------------------------------------

def espn_payload():
    def player(pid, name, position_id, team_id, stats):
        lines = [{"statSourceId": 0, "scoringPeriodId": WEEK, "statSplitTypeId": 1, "stats": {"42": 999}}]
        if stats is not None:
            lines.append({"statSourceId": 1, "scoringPeriodId": WEEK, "statSplitTypeId": 1, "stats": stats})
        return {"player": {"id": pid, "fullName": name, "defaultPositionId": position_id,
                           "proTeamId": team_id, "stats": lines}}
    return {"players": [
        player(1, "Ja'Marr Chase", 3, 4, {"42": 91.0, "43": 0.8, "53": 8.0, "44": 0.02}),
        player(2, "Bye Week Guy", 3, 2, None),
        player(3, "Some Kicker", 5, 16, {"80": 2.0}),
    ]}


def test_espn_rows_map_stats_and_skip_byes(session, seeded):
    rows = espn.projection_rows(espn_payload(), SEASON, WEEK, PlayerMatcher(session))
    (row,) = rows
    assert (row["player_id"], row["team"], row["position"]) == (seeded["chase"], "CIN", "WR")
    assert json.loads(row["stats"]) == {"rec_yds": 91.0, "rec_tds": 0.8, "rec": 8.0, "two_pt": 0.02}


def test_espn_refresh_sends_week_filter(session, seeded):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=espn_payload())

    assert espn.refresh(session, mock_client(handler), SEASON, WEEK) == 1
    player_filter = json.loads(seen[0].headers["X-Fantasy-Filter"])
    assert player_filter["players"]["filterStatsForTopScoringPeriodIds"]["additionalValue"] == ["1120267"]
    assert seen[0].url.params["scoringPeriodId"] == "7"
    with pytest.raises(SourceError, match="no players"):
        espn.refresh(session, mock_client(lambda r: httpx.Response(200, json={"players": []})), SEASON, WEEK)


# ---- Refresh orchestration ----------------------------------------------------------------

def test_refresh_sources_isolates_failures_and_caches(session, seeded, monkeypatch):
    def yahoo_ok(session, http, season, week):
        return yahoo_experts.refresh(session, mock_client(lambda r: httpx.Response(
            200, json=yahoo_payload(r.url.params["position"]))), season, week)

    def espn_down(session, http, season, week):
        raise SourceError("ESPN projections returned HTTP 503.")

    monkeypatch.setitem(weekly_refresh.FREE_SOURCES, yahoo_experts.SOURCE, yahoo_ok)
    monkeypatch.setitem(weekly_refresh.FREE_SOURCES, espn.SOURCE, espn_down)
    http = mock_client(lambda r: httpx.Response(500))
    statuses = weekly_refresh.refresh_sources(session, SEASON, WEEK, Settings(), http=http)
    assert statuses["yahoo_experts"] == {"state": "updated", "rows": 12, "message": None}
    assert statuses["espn"]["state"] == "error" and "503" in statuses["espn"]["message"]
    assert statuses["vegas_props"]["state"] == "not_configured"

    again = weekly_refresh.refresh_sources(session, SEASON, WEEK, Settings(), http=http)
    assert again["yahoo_experts"]["state"] == "fresh"
    forced = weekly_refresh.refresh_sources(session, SEASON, WEEK, Settings(), force=True, http=http)
    assert forced["yahoo_experts"]["state"] == "updated"


def test_refresh_sources_reports_props_errors(session, seeded):
    statuses = weekly_refresh.refresh_sources(
        session, SEASON, WEEK, Settings(odds_api_key="k", weekly_sources=["vegas_props"]),
        http=mock_client(lambda r: httpx.Response(200, json=[])))
    assert statuses["vegas_props"] == {"state": "error", "rows": None,
                                       "message": "no upcoming week 7 games in the schedule (run populate_stats)"}
    assert statuses["espn"]["state"] == "disabled"


def test_refresh_sources_reports_props_purchases(session, seeded):
    _add_games(session, ("CIN", "BAL"))
    http = mock_client(odds_handler([], [_event("evt1", "Cincinnati Bengals", "Baltimore Ravens")]))
    settings = Settings(odds_api_key="k", weekly_sources=["vegas_props"])
    first = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, http=http)
    assert first["vegas_props"] == {"state": "updated", "rows": 1,
                                    "message": "Bought props for 1 games (400 credits left this month)."}
    again = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, force=True, http=http)
    assert again["vegas_props"]["state"] == "fresh"           # force never re-buys props
    rebuy = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, http=http, force_props=True)
    assert rebuy["vegas_props"]["state"] == "updated"


def test_refresh_sources_turns_unexpected_errors_into_statuses(session, seeded, monkeypatch, caplog):
    def broken(session, http, season, week):
        return [][0]                                          # e.g. an unofficial API changed shape

    monkeypatch.setitem(weekly_refresh.FREE_SOURCES, yahoo_experts.SOURCE, broken)
    settings = Settings(weekly_sources=["yahoo_experts"])
    http = mock_client(lambda r: httpx.Response(500))
    statuses = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, http=http)
    assert statuses["yahoo_experts"] == {"state": "error", "rows": None,
                                         "message": "Yahoo returned data this app couldn't read; kept the last snapshot."}
    assert "yahoo_experts refresh failed unexpectedly" in caplog.text
    # The failure is logged, so the next refresh backs off instead of hammering Yahoo.
    again = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, http=http)
    assert "retrying after 30 min" in again["yahoo_experts"]["message"]


def test_refresh_sources_runs_one_at_a_time(session, seeded):
    assert weekly_refresh._refresh_lock.acquire(blocking=False)
    try:
        statuses = weekly_refresh.refresh_sources(session, SEASON, WEEK, Settings(),
                                                  http=mock_client(lambda r: httpx.Response(500)))
    finally:
        weekly_refresh._refresh_lock.release()
    assert {s["state"] for s in statuses.values()} == {"busy"}
    assert set(statuses) == {"yahoo_experts", "espn", "vegas_props"}


def test_weekly_refresh_cli(engine, seeded, monkeypatch, caplog):
    import database.db as db

    seen = []
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(weekly_refresh, "refresh_sources", lambda session, season, week, **kw: seen.append(kw) or
                        {"espn": {"state": "updated", "rows": 3, "message": None}})
    with caplog.at_level("INFO"):
        weekly_refresh.main(["--week", "8", "--force-props"])
    assert "espn (week 8): updated, 3 rows" in caplog.text
    assert seen == [{"force": False, "force_props": True}]


def test_refresh_sources_skips_disabled_sources(session, seeded, monkeypatch):
    calls = []
    monkeypatch.setitem(weekly_refresh.FREE_SOURCES, espn.SOURCE, lambda *args: calls.append("espn") or 1)
    monkeypatch.setitem(weekly_refresh.FREE_SOURCES, yahoo_experts.SOURCE, lambda *args: calls.append("yahoo") or 1)
    settings = Settings(weekly_sources=["yahoo_experts"], odds_api_key="k")
    statuses = weekly_refresh.refresh_sources(session, SEASON, WEEK, settings, http=mock_client(lambda r: httpx.Response(500)))
    assert calls == ["yahoo"]
    assert statuses["espn"]["state"] == statuses["vegas_props"]["state"] == "disabled"
