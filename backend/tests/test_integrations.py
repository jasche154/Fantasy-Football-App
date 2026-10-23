import base64
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from cryptography.fernet import Fernet

from config import Settings
from database.models import ExpertRanking, League, Roster, Team
from integrations import fantasypros, sleeper, token_crypto, yahoo
from services import yahoo_sync

KEY = Fernet.generate_key().decode()


def mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ---- Sleeper ----------------------------------------------------------------------------

def test_sleeper_trending_request_and_cache():
    sleeper._cache.clear()
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=[{"player_id": "1", "count": 5}])

    client = mock_client(handler)
    assert sleeper.fetch_trending("add", 3, client=client) == [{"player_id": "1", "count": 5}]
    sleeper.fetch_trending("add", 3, client=client)
    assert len(seen) == 1                                     # second call served from cache
    assert seen[0].url.path == "/v1/players/nfl/trending/add" and seen[0].url.params["limit"] == "3"


@pytest.mark.parametrize("response", [httpx.Response(500), httpx.Response(200, json={"oops": 1}),
                                      httpx.Response(200, content=b"not json")])
def test_sleeper_errors(response):
    sleeper._cache.clear()
    with pytest.raises(sleeper.SleeperError):
        sleeper.fetch_trending("drop", client=mock_client(lambda request: response))
    with pytest.raises(ValueError):
        sleeper.fetch_trending("hold")


# ---- FantasyPros ------------------------------------------------------------------------

FP_PAYLOAD = {"players": [
    {"player_id": 9001, "player_name": "Sell High Guy", "player_team_id": "SEL", "player_positions": "RB",
     "rank_ecr": 1, "rank_ave": "1.4", "rank_std": "0.5", "rank_min": "1", "rank_max": "3", "pos_rank": "RB1"},
    {"player_id": 42, "player_name": "Unknown Guy", "player_positions": "WR,RB", "rank_ecr": "2"},
    {"player_id": 43, "player_name": "No Rank"},
]}


@pytest.fixture
def no_rate_limit(monkeypatch):
    monkeypatch.setattr(fantasypros.rate_limiter, "acquire", lambda: None)


def test_fantasypros_request_uses_key_header_and_params(no_rate_limit):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=FP_PAYLOAD)

    client = fantasypros.FantasyProsClient("secret", http=mock_client(handler))
    client.consensus_rankings(2026, "RB", "ROS", "HALF", week=7)
    request = seen[0]
    assert request.headers["x-api-key"] == "secret"
    assert request.url.path == "/public/v2/json/nfl/2026/consensus-rankings"
    assert dict(request.url.params) == {"position": "RB", "type": "ROS", "scoring": "HALF", "week": "7"}
    with pytest.raises(ValueError):
        client.consensus_rankings(2026, ranking_type="WEEKLY")


@pytest.mark.parametrize("status, message", [(401, "rejected the API key"), (500, "HTTP 500")])
def test_fantasypros_http_errors(no_rate_limit, status, message):
    client = fantasypros.FantasyProsClient("k", http=mock_client(lambda r: httpx.Response(status)))
    with pytest.raises(fantasypros.FantasyProsError, match=message):
        client.consensus_rankings(2026)


def test_fantasypros_requires_key():
    with pytest.raises(fantasypros.FantasyProsError, match="FANTASYPROS_API_KEY"):
        fantasypros.FantasyProsClient("")


def test_rate_limiter_spaces_calls(monkeypatch):
    sleeps = []
    monkeypatch.setattr(fantasypros.time, "sleep", sleeps.append)
    limiter = fantasypros._RateLimiter()
    limiter.acquire()
    limiter.acquire()
    assert len(sleeps) == 1 and 0 < sleeps[0] <= fantasypros.MIN_SECONDS_BETWEEN_CALLS


def fp_client(handler) -> fantasypros.FantasyProsClient:
    return fantasypros.FantasyProsClient("k", http=mock_client(handler))


def test_daily_cap_is_persisted_in_the_database(session, seeded, no_rate_limit):
    from database.models import ExternalApiCall

    session.add_all([ExternalApiCall(source=fantasypros.SOURCE, ok=True)
                     for _ in range(fantasypros.DAILY_CALL_LIMIT)])
    session.commit()
    calls = []
    client = fp_client(lambda r: calls.append(r) or httpx.Response(200, json=FP_PAYLOAD))
    with pytest.raises(fantasypros.FantasyProsError, match="daily call limit"):
        fantasypros.refresh_rankings(session, client, 2026)
    assert calls == []


def test_failures_back_off_and_keep_the_previous_snapshot(session, seeded, no_rate_limit):
    good = fp_client(lambda r: httpx.Response(200, json=FP_PAYLOAD))
    assert fantasypros.refresh_rankings(session, good, 2026) == 2
    empty = fp_client(lambda r: httpx.Response(200, json={"players": []}))
    with pytest.raises(fantasypros.FantasyProsError, match="no rankings"):
        fantasypros.refresh_rankings(session, empty, 2026, force=True)
    assert session.query(ExpertRanking).filter_by(source="fantasypros_api").count() == 2
    calls = []
    retry = fp_client(lambda r: calls.append(r) or httpx.Response(200, json=FP_PAYLOAD))
    with pytest.raises(fantasypros.FantasyProsError, match="retrying after"):
        fantasypros.refresh_rankings(session, retry, 2026, force=True)
    assert calls == []                                       # backing off, no API call


def test_api_errors_are_logged_as_failed_calls(session, seeded, no_rate_limit):
    broken = fp_client(lambda r: httpx.Response(500))
    with pytest.raises(fantasypros.FantasyProsError, match="HTTP 500"):
        fantasypros.refresh_rankings(session, broken, 2026)
    assert fantasypros.last_failure(session) is not None
    assert fantasypros.calls_in_last_day(session) == 1


def test_refresh_needs_a_season(session, no_rate_limit):
    with pytest.raises(fantasypros.FantasyProsError, match="no season"):
        fantasypros.refresh_rankings(session, fp_client(lambda r: httpx.Response(200)), None)


def test_ranking_rows_normalize_payload():
    rows = fantasypros.ranking_rows(FP_PAYLOAD, 2026, "ROS", "PPR", {"9001": 7})
    assert len(rows) == 2
    first, second = rows
    assert (first["player_id"], first["ecr"], first["sd"], first["best"], first["worst"]) == (7, 1.4, 0.5, 1, 3)
    assert (second["player_id"], second["position"], second["ecr"]) == (None, "WR", 2.0)


def test_refresh_rankings_replaces_snapshot_and_respects_cache(session, seeded, no_rate_limit):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=FP_PAYLOAD)

    client = fantasypros.FantasyProsClient("k", http=mock_client(handler))
    assert fantasypros.refresh_rankings(session, client, 2026) == 2
    assert fantasypros.refresh_rankings(session, client, 2026) is None       # still fresh
    assert len(calls) == 1
    stored = session.query(ExpertRanking).filter_by(source="fantasypros_api").all()
    assert {r.player_name for r in stored} == {"Sell High Guy", "Unknown Guy"}
    assert next(r for r in stored if r.rank == 1).player_id == seeded["sell"]
    assert fantasypros.refresh_rankings(session, client, 2026, force=True) == 2
    assert session.query(ExpertRanking).filter_by(source="fantasypros_api").count() == 2


def test_expert_endpoint_prefers_fantasypros_and_reports_failures(client, monkeypatch):
    from config import get_settings

    monkeypatch.setenv("FANTASYPROS_API_KEY", "k")
    get_settings.cache_clear()

    def failing_refresh(*_args, **_kwargs):
        raise fantasypros.FantasyProsError("quota")

    monkeypatch.setattr("api.data_routes.refresh_rankings", failing_refresh)
    body = client.get("/api/experts/rankings").json()
    assert "quota" in body["warning"] and body["source"] == "nflverse_ffrankings"


# ---- Token encryption -------------------------------------------------------------------

def test_token_round_trip_and_errors():
    secret = token_crypto.encrypt("abc", KEY)
    assert secret != "abc" and token_crypto.decrypt(secret, KEY) == "abc"
    assert token_crypto.encrypt(None, KEY) is None and token_crypto.decrypt(None, KEY) is None
    with pytest.raises(token_crypto.TokenCryptoError, match="not set"):
        token_crypto.encrypt("abc", None)
    with pytest.raises(token_crypto.TokenCryptoError, match="not a valid"):
        token_crypto.encrypt("abc", "short")
    with pytest.raises(token_crypto.TokenCryptoError, match="can't be decrypted"):
        token_crypto.decrypt(secret, Fernet.generate_key().decode())


# ---- Yahoo ------------------------------------------------------------------------------

def yahoo_settings(**overrides) -> Settings:
    values = {"yahoo_client_id": "cid", "yahoo_client_secret": "csecret",
              "yahoo_redirect_uri": "https://localhost:8000/callback", "token_encryption_key": KEY}
    return Settings(_env_file=None, **{**values, **overrides})


LEAGUES_JSON = {"fantasy_content": {"users": {"0": {"user": [{"guid": "G"}, {"games": {"0": {"game": [
    {"game_key": "461", "code": "nfl"},
    {"leagues": {"0": {"league": [{"league_key": "461.l.1", "name": "Champs", "season": "2026",
                                   "num_teams": 2, "scoring_type": "head", "current_week": 7}]},
                 "count": 1}}]}, "count": 1}}]}, "count": 1}}}
TEAMS_JSON = {"fantasy_content": {"league": [{"league_key": "461.l.1"}, {"teams": {
    "0": {"team": [[{"team_key": "461.l.1.t.1"}, {"name": "Mine"}, {"is_owned_by_current_login": 1},
                    {"managers": [{"manager": {"nickname": "Me"}}]}]]},
    "1": {"team": [[{"team_key": "461.l.1.t.2"}, {"name": "Theirs"}, [],
                    {"managers": [{"manager": {"nickname": "Rival"}}]}]]},
    "count": 2}}]}}


def roster_json(yahoo_ids: list[str]) -> dict:
    players = {str(i): {"player": [[{"player_id": pid}, {"name": {"full": f"Yahoo {pid}"}},
                                    {"display_position": "WR"}],
                                   {"selected_position": [{"coverage_type": "week"}, {"position": "WR"}]}]}
               for i, pid in enumerate(yahoo_ids)}
    return {"fantasy_content": {"team": [[{"team_key": "t"}], {"roster": {"0": {"players": {
        **players, "count": len(yahoo_ids)}}}}]}}


def test_authorization_url_contains_required_params():
    url = httpx.URL(yahoo.authorization_url("cid", "https://localhost:8000/callback", "s1"))
    assert str(url).startswith(yahoo.AUTH_URL)
    assert dict(url.params) == {"client_id": "cid", "redirect_uri": "https://localhost:8000/callback",
                                "response_type": "code", "state": "s1"}


def test_exchange_code_uses_basic_auth_form_body():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"access_token": "A", "refresh_token": "R", "expires_in": 3600})

    token = yahoo.exchange_code(mock_client(handler), "cid", "csecret", "https://x/cb", "CODE")
    request = seen[0]
    assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"cid:csecret").decode()
    assert dict(httpx.QueryParams(request.content.decode())) == {
        "grant_type": "authorization_code", "code": "CODE", "redirect_uri": "https://x/cb"}
    assert token.access_token == "A" and not token.is_expired()


def test_refresh_keeps_old_refresh_token_when_not_rotated_and_errors():
    client = mock_client(lambda r: httpx.Response(200, json={"access_token": "B", "expires_in": 3600}))
    old = yahoo.YahooToken("A", "R", 0)
    assert old.is_expired()
    new = yahoo.refresh_token(client, "cid", "cs", "uri", old)
    assert (new.access_token, new.refresh_token) == ("B", "R")
    with pytest.raises(yahoo.YahooAuthError):
        yahoo.refresh_token(client, "cid", "cs", "uri", yahoo.YahooToken("A", None, 0))
    with pytest.raises(yahoo.YahooAuthError):
        yahoo.exchange_code(mock_client(lambda r: httpx.Response(400)), "c", "s", "u", "bad")
    yahoo_says = mock_client(lambda r: httpx.Response(401, json={"error": "invalid_client",
                                                                   "error_description": "client authentication failed"}))
    with pytest.raises(yahoo.YahooAuthError, match="invalid_client - client authentication failed"):
        yahoo.exchange_code(yahoo_says, "c", "s", "u", "bad")


def test_api_get_requests_json_with_bearer_and_maps_errors():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"fantasy_content": {"ok": True}})

    token = yahoo.YahooToken("A", "R", 9e12)
    assert yahoo.api_get(mock_client(handler), token, "/league/1/teams") == {"ok": True}
    assert seen[0].url.params["format"] == "json" and seen[0].headers["Authorization"] == "Bearer A"
    with pytest.raises(yahoo.YahooAuthError):
        yahoo.api_get(mock_client(lambda r: httpx.Response(401)), token, "x")
    with pytest.raises(yahoo.YahooError):
        yahoo.api_get(mock_client(lambda r: httpx.Response(500)), token, "x")
    with pytest.raises(yahoo.YahooError):
        yahoo.api_get(mock_client(lambda r: httpx.Response(200, json={"nope": 1})), token, "x")


def test_yahoo_json_parsers():
    assert yahoo.parse_leagues(LEAGUES_JSON["fantasy_content"]) == [{
        "league_key": "461.l.1", "name": "Champs", "season": 2026, "num_teams": 2, "scoring_type": "head",
        "current_week": 7}]
    teams = yahoo.parse_teams(TEAMS_JSON["fantasy_content"])
    assert teams == [{"team_key": "461.l.1.t.1", "name": "Mine", "owner": "Me", "is_owned_by_current_login": True},
                     {"team_key": "461.l.1.t.2", "name": "Theirs", "owner": "Rival",
                      "is_owned_by_current_login": False}]
    roster = yahoo.parse_roster(roster_json(["555"])["fantasy_content"])
    assert roster == [{"yahoo_player_id": "555", "name": "Yahoo 555", "position": "WR", "slot": "WR"}]
    assert yahoo.collection(None, "x") == [] and yahoo.merge({"a": 1}) == {"a": 1}
    assert yahoo.league_team_keys(teams) == ["461.l.1.t.1", "461.l.1.t.2"]


REDIRECT = "https://localhost:8000/callback"


def issue_state(settings: Settings | None = None) -> str:
    url = yahoo_sync.start_authorization(settings or yahoo_settings())
    return dict(httpx.URL(url).params)["state"]


def test_yahoo_state_is_signed_expires_and_survives_restarts(monkeypatch):
    settings = yahoo_settings()
    state = issue_state(settings)
    yahoo_sync.check_state(state, settings)                   # no server memory needed (restart-safe)
    yahoo_sync.check_state(state, yahoo_settings())           # a "restarted" process with the same key
    with pytest.raises(yahoo.YahooAuthError, match="expired or wasn't created"):
        yahoo_sync.check_state(state, yahoo_settings(token_encryption_key=Fernet.generate_key().decode()))
    with pytest.raises(yahoo.YahooAuthError, match="expired or wasn't created"):
        yahoo_sync.check_state("forged-state", settings)
    monkeypatch.setattr(yahoo_sync, "STATE_TTL_SECONDS", -1)
    with pytest.raises(yahoo.YahooAuthError, match="expired"):
        yahoo_sync.check_state(state, settings)
    with pytest.raises(yahoo.YahooAuthError, match="whole redirected URL"):
        yahoo_sync.check_state(None, settings)                # bare code can't skip the check
    yahoo_sync.check_state(None, yahoo_settings(yahoo_redirect_uri="oob"))


def test_yahoo_config_and_key_checked_before_any_oauth_step(session, seeded):
    with pytest.raises(yahoo_sync.YahooNotConfigured):
        yahoo_sync.start_authorization(yahoo_settings(yahoo_client_id=None))
    with pytest.raises(token_crypto.TokenCryptoError):
        yahoo_sync.start_authorization(yahoo_settings(token_encryption_key=None))
    http, seen = yahoo_api()
    with pytest.raises(token_crypto.TokenCryptoError):
        yahoo_sync.connect(session, yahoo_settings(token_encryption_key="bad"), http, "CODE", None)
    assert seen == []                                        # the one-time code was not spent


def yahoo_api(handler_overrides: dict | None = None):
    responses = {"/oauth2/get_token": {"access_token": "A2", "refresh_token": "R2", "expires_in": 3600},
                 "users;use_login=1/games;game_keys=nfl/leagues": LEAGUES_JSON,
                 "league/461.l.1/teams": TEAMS_JSON,
                 "team/461.l.1.t.1/roster;week=7": roster_json(["555", "999"]),
                 "team/461.l.1.t.2/roster;week=7": roster_json([])}
    responses.update(handler_overrides or {})
    seen = []

    def handler(request):
        seen.append(request)
        path = request.url.path
        key = path if path == "/oauth2/get_token" else path.removeprefix("/fantasy/v2/")
        return httpx.Response(200, json=responses[key])

    return mock_client(handler), seen


def test_connect_stores_encrypted_tokens_and_import_league(session, seeded):
    settings = yahoo_settings()
    http, seen = yahoo_api()
    yahoo_sync.connect(session, settings, http, "CODE", issue_state(settings))
    connection = yahoo_sync.get_connection(session)
    assert connection.access_token != "A2"
    assert yahoo_sync.load_token(connection, settings).access_token == "A2"

    result = yahoo_sync.import_league(session, settings, http, "461.l.1")
    assert result["teams"] == 2 and result["roster_spots"] == 1 and result["unmatched_players"] == ["Yahoo 999"]
    league = session.query(League).one()
    assert league.name == "Champs" and json.loads(league.scoring_settings)["num_teams"] == 2
    mine = session.query(Team).filter_by(is_users_team=True).one()
    assert mine.name == "Mine" and mine.owner_display_name == "Me"
    assert session.query(Roster).one().player_id == seeded["chase"]

    yahoo_sync.import_league(session, settings, http, "461.l.1")       # re-import replaces, not duplicates
    assert session.query(Roster).count() == 1 and session.query(Team).count() == 2
    with pytest.raises(LookupError):
        yahoo_sync.import_league(session, settings, http, "461.l.404")


def test_expired_token_is_refreshed_before_api_calls(session, seeded):
    settings = yahoo_settings()
    http, seen = yahoo_api()
    yahoo_sync.save_token(session, settings, yahoo.YahooToken("OLD", "R", 0))
    yahoo_sync.list_leagues(session, settings, http)
    assert seen[0].url.path == "/oauth2/get_token"
    assert yahoo_sync.load_token(yahoo_sync.get_connection(session), settings).access_token == "A2"
    expires = yahoo_sync.get_connection(session).token_expires_at
    expires = expires if expires.tzinfo else expires.replace(tzinfo=timezone.utc)
    assert expires > datetime.now(timezone.utc) + timedelta(minutes=30)


def test_list_leagues_requires_connection(session, seeded):
    with pytest.raises(yahoo.YahooAuthError, match="isn't connected"):
        yahoo_sync.list_leagues(session, yahoo_settings(), yahoo_api()[0])


def test_parse_code_accepts_url_or_bare_code():
    from fastapi import HTTPException

    from api.integration_routes import parse_code

    assert parse_code("abc123") == ("abc123", None)
    assert parse_code("https://localhost:8000/callback?code=xyz&state=s9") == ("xyz", "s9")
    assert parse_code(" code=xyz ") == ("xyz", None)
    with pytest.raises(HTTPException):
        parse_code("https://localhost/callback?code=")

# ---- Integration endpoints --------------------------------------------------------------

def test_integration_status_endpoint(client):
    assert client.get("/api/integrations/status").json() == {
        "fantasypros": False, "yahoo_configured": False, "yahoo_connected": False}
    assert client.get("/api/meta").json()["integrations"]["yahoo_connected"] is False


def test_trending_maps_sleeper_ids_and_handles_outages(client, seeded, monkeypatch):
    import integrations.sleeper as sleeper

    monkeypatch.setattr(sleeper, "fetch_trending", lambda *a, **k: [{"player_id": "111", "count": 9},
                                                                    {"player_id": "DAL", "count": 3}])
    rows = client.get("/api/insights/trending").json()["results"]
    assert rows[0]["player"]["id"] == seeded["buy"] and rows[1]["player"] is None

    def down(*_args, **_kwargs):
        raise sleeper.SleeperError("boom")

    monkeypatch.setattr(sleeper, "fetch_trending", down)
    assert client.get("/api/insights/trending").status_code == 502


def test_yahoo_endpoints_without_configuration(client):
    assert client.get("/api/integrations/yahoo/auth-url").status_code == 503
    assert client.post("/api/integrations/yahoo/exchange", json={"code": "abc"}).status_code == 503
    assert client.get("/api/integrations/yahoo/leagues").status_code == 401
