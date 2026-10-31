"""Yahoo Fantasy Sports API: OAuth 2.0 (authorization code) and read-only league data.

OAuth docs: https://developer.yahoo.com/oauth2/guide/flows_authcode/
API docs:   https://sports.yahoo.com/developer/docs/
Yahoo requires showing "Fantasy data provided by Yahoo Fantasy" wherever its data appears.

Yahoo's JSON wraps collections as {"0": {...}, "1": {...}, "count": n} and splits entities
into lists of single-key dicts. `collection()` and `merge()` flatten both.
"""
import base64
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API_BASE = "https://fantasysports.yahooapis.com/fantasy/v2"
TIMEOUT_SECONDS = 15.0
EXPIRY_MARGIN_SECONDS = 60


class YahooError(RuntimeError):
    pass


class YahooAuthError(YahooError):
    """The stored token is missing, expired, or revoked; the user must reconnect."""


@dataclass(frozen=True)
class YahooToken:
    access_token: str
    refresh_token: str | None
    expires_at: float          # unix seconds

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expires_at - EXPIRY_MARGIN_SECONDS


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    # No `scope`: Yahoo then uses the permissions chosen when the app was registered (Fantasy Sports: Read).
    params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "state": state}
    return f"{AUTH_URL}?{urlencode(params)}"


def _yahoo_reason(response: httpx.Response) -> str:
    """Yahoo's error code and description ("invalid_grant: ..."); never includes secrets."""
    try:
        payload = response.json()
    except ValueError:
        return ""
    if not isinstance(payload, dict):
        return ""
    parts = [str(payload[k]) for k in ("error", "error_description") if payload.get(k)]
    return f": {' - '.join(parts)[:200]}" if parts else ""


def _token_request(http: httpx.Client, client_id: str, client_secret: str, form: dict) -> YahooToken:
    credentials = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        response = http.post(TOKEN_URL, data=form, headers={
            "Authorization": f"Basic {credentials}", "Content-Type": "application/x-www-form-urlencoded"})
    except httpx.HTTPError as exc:
        raise YahooError(f"Yahoo token request failed ({type(exc).__name__}).") from exc
    if response.status_code >= 400:
        raise YahooAuthError(f"Yahoo rejected the sign-in (HTTP {response.status_code}{_yahoo_reason(response)}). "
                             "Check that YAHOO_CLIENT_ID, YAHOO_CLIENT_SECRET and YAHOO_REDIRECT_URI match your "
                             "Yahoo app exactly, then click Connect Yahoo again (each code works once).")
    payload = response.json()
    return YahooToken(access_token=payload["access_token"], refresh_token=payload.get("refresh_token"),
                      expires_at=time.time() + int(payload.get("expires_in", 3600)))


def exchange_code(http: httpx.Client, client_id: str, client_secret: str, redirect_uri: str,
                  code: str) -> YahooToken:
    return _token_request(http, client_id, client_secret, {
        "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri})


def refresh_token(http: httpx.Client, client_id: str, client_secret: str, redirect_uri: str,
                  token: YahooToken) -> YahooToken:
    if not token.refresh_token:
        raise YahooAuthError("No Yahoo refresh token stored; reconnect Yahoo.")
    refreshed = _token_request(http, client_id, client_secret, {
        "grant_type": "refresh_token", "refresh_token": token.refresh_token, "redirect_uri": redirect_uri})
    # Yahoo may or may not rotate the refresh token; keep the old one if it doesn't.
    return refreshed if refreshed.refresh_token else YahooToken(
        refreshed.access_token, token.refresh_token, refreshed.expires_at)


def _api_reason(response: httpx.Response) -> str:
    """Yahoo's error description from an API error body ({"error": {"description": ...}})."""
    try:
        error = response.json().get("error") or {}
        description = error.get("description") if isinstance(error, dict) else None
    except (ValueError, AttributeError):
        return ""
    return f' ("{str(description)[:200]}")' if description else ""


def api_get(http: httpx.Client, token: YahooToken, path: str) -> dict:
    try:
        response = http.get(f"{API_BASE}/{path.lstrip('/')}", params={"format": "json"},
                            headers={"Authorization": f"Bearer {token.access_token}"})
    except httpx.HTTPError as exc:
        raise YahooError(f"Yahoo API request failed ({type(exc).__name__}).") from exc
    if response.status_code == 401:
        raise YahooAuthError("Yahoo access token was rejected.")
    if response.status_code == 403:
        raise YahooError(f"Yahoo refused the request{_api_reason(response)}. Your Yahoo app isn't allowed to "
                         "use the Fantasy Sports API: enable Fantasy Sports (Read) for the app at "
                         "developer.yahoo.com, or get Yahoo's approval for Fantasy API access.")
    if response.status_code >= 400:
        raise YahooError(f"Yahoo API returned HTTP {response.status_code} for {path}{_api_reason(response)}.")
    try:
        return response.json()["fantasy_content"]
    except (ValueError, KeyError) as exc:
        raise YahooError("Unexpected Yahoo API response.") from exc


# ---- JSON helpers -----------------------------------------------------------------------

def merge(parts: Any) -> dict:
    """Flatten Yahoo's [{"a": 1}, {"b": 2}, [{"c": 3}]] entity lists into one dict."""
    if isinstance(parts, dict):
        return dict(parts)
    merged: dict = {}
    for part in parts or []:
        if isinstance(part, list):
            merged.update(merge(part))
        elif isinstance(part, dict):
            merged.update(part)
    return merged


def collection(container: Any, singular: str) -> list[Any]:
    """Items of a {"0": {singular: x}, "count": n} collection, in order."""
    if not isinstance(container, dict):
        return []
    count = int(container.get("count", 0))
    return [container[str(i)][singular] for i in range(count)
            if isinstance(container.get(str(i)), dict) and singular in container[str(i)]]


def parse_leagues(content: dict) -> list[dict]:
    """Leagues from /users;use_login=1/games;game_keys=nfl/leagues."""
    leagues = []
    for user in collection(content.get("users"), "user"):
        for game in collection(merge(user).get("games"), "game"):
            for league in collection(merge(game).get("leagues"), "league"):
                meta = merge(league)
                leagues.append({
                    "league_key": meta.get("league_key"), "name": meta.get("name"),
                    "season": int(meta["season"]) if meta.get("season") else None,
                    "num_teams": int(meta["num_teams"]) if meta.get("num_teams") else None,
                    "scoring_type": meta.get("scoring_type"),
                    "current_week": int(meta["current_week"]) if meta.get("current_week") else None,
                })
    return leagues


def _manager_name(meta: dict) -> str | None:
    """Team managers arrive as [{"manager": {...}}] (or occasionally a keyed collection)."""
    managers = meta.get("managers") or []
    items = (collection(managers, "manager") if isinstance(managers, dict)
             else [m.get("manager") for m in managers if isinstance(m, dict)])
    first = merge(items[0]) if items and items[0] else {}
    return first.get("nickname")


def parse_teams(content: dict) -> list[dict]:
    """Teams from /league/{league_key}/teams."""
    league = content.get("league") or []
    teams_container = merge(league[1:]).get("teams") if len(league) > 1 else None
    teams = []
    for team in collection(teams_container, "team"):
        meta = merge(team)
        teams.append({
            "team_key": meta.get("team_key"), "name": meta.get("name"),
            "owner": _manager_name(meta),
            "is_owned_by_current_login": str(meta.get("is_owned_by_current_login", "0")) == "1",
        })
    return teams


def parse_roster(content: dict) -> list[dict]:
    """Players and lineup slots from /team/{team_key}/roster."""
    team = content.get("team") or []
    roster = merge(team[1:]).get("roster", {}) if len(team) > 1 else {}
    players_container = (roster.get("0") or {}).get("players")
    players = []
    for player in collection(players_container, "player"):
        meta = merge(player)
        slot = merge(meta.get("selected_position"))
        name = meta.get("name") or {}
        players.append({
            "yahoo_player_id": str(meta.get("player_id")) if meta.get("player_id") else None,
            "name": name.get("full") if isinstance(name, dict) else name,
            "position": meta.get("display_position"), "slot": slot.get("position"),
            "team": meta.get("editorial_team_abbr"),
        })
    return players


RECEPTION_STAT_ID = 11
# Yahoo roster slots -> this app's starter keys (K and DEF aren't modeled).
SLOT_MAP = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "W/R/T": "FLEX", "W/R": "FLEX", "W/T": "FLEX",
            "R/T": "FLEX", "K": "K", "DEF": "DST"}
SUPERFLEX_SLOTS = {"Q/W/R/T", "SUPERFLEX", "OP"}


def _number(value: object, kind: type) -> int | float:
    """Yahoo sends numbers as strings; anything unreadable or negative counts as 0."""
    try:
        number = kind(value)
    except (TypeError, ValueError):
        return kind(0)
    return number if number >= 0 else kind(0)


def parse_settings(content: dict) -> dict:
    """Scoring preset, starter slots and bench size from /league/{league_key}/settings."""
    league = content.get("league") or []
    settings = merge(merge(league[1:]).get("settings")) if len(league) > 1 else {}
    starters: dict[str, int] = {}
    bench, superflex = 0, False
    for item in (settings.get("roster_positions") or []):
        slot = (item or {}).get("roster_position") or {} if isinstance(item, dict) else {}
        position, count = slot.get("position"), _number(slot.get("count"), int)
        if position == "BN":
            bench += count
        elif position in SUPERFLEX_SLOTS:
            superflex = True
        elif position in SLOT_MAP:
            key = SLOT_MAP[position]
            starters[key] = starters.get(key, 0) + count
    reception = 0.0
    modifiers = settings.get("stat_modifiers")
    from services.kdst_model import DEFAULT_DST, DEFAULT_K, YAHOO_DST, YAHOO_K

    k_scoring, dst_scoring = dict(DEFAULT_K), dict(DEFAULT_DST)
    for item in ((modifiers if isinstance(modifiers, dict) else {}).get("stats") or []):
        stat = (item or {}).get("stat") or {} if isinstance(item, dict) else {}
        stat_id = _number(stat.get("stat_id"), int)
        if stat_id == RECEPTION_STAT_ID:
            reception = _number(stat.get("value"), float)
        elif stat_id in YAHOO_K or stat_id in YAHOO_DST:
            try:
                value = float(stat.get("value"))
            except (TypeError, ValueError):
                continue
            if -20 <= value <= 20:                       # anything outside this is corrupt data
                (k_scoring if stat_id in YAHOO_K else dst_scoring)[(YAHOO_K | YAHOO_DST)[stat_id]] = value
    scoring = "ppr" if reception >= 0.75 else "half" if reception >= 0.25 else "std"
    rules = {"scoring": scoring, "reception_points": reception, "starters": starters, "bench": bench,
             "superflex": superflex, "k_scoring": k_scoring, "dst_scoring": dst_scoring}
    start = _number(settings.get("playoff_start_week"), int)
    if str(settings.get("uses_playoff", "1")) == "1" and 1 <= start <= 18:
        teams = _number(settings.get("num_playoff_teams"), int) or 6
        rounds = max(1, (max(teams, 2) - 1).bit_length())          # 4 teams: 2 rounds, 6 or 8: 3
        rules["playoff_weeks"] = [w for w in range(start, start + rounds) if w <= 18]
        rules["playoff_teams"] = teams
    # Waivers: FAAB, a rolling priority list, or free agency only (see services/waiver_advice.py).
    rules["uses_faab"] = str(settings.get("uses_faab", "0")) == "1"
    rules["waiver_type"] = settings.get("waiver_type") or None
    rules["waiver_rule"] = settings.get("waiver_rule") or None
    budget = _number(settings.get("faab_budget"), int)
    rules["faab_budget"] = budget or (100 if rules["uses_faab"] else None)
    if settings.get("trade_end_date"):
        rules["trade_end_date"] = str(settings.get("trade_end_date"))
    return rules


def _float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def parse_scoreboard(content: dict) -> list[dict]:
    """Matchups from /league/{key}/scoreboard: each team's key, name, points, projection, win probability."""
    league = content.get("league") or []
    board = merge(league[1:]).get("scoreboard") if len(league) > 1 else None
    if not isinstance(board, dict):
        return []
    matchups = (board.get("0") or {}).get("matchups")
    out = []
    for matchup in collection(matchups, "matchup"):
        teams = []
        for team in collection((matchup.get("0") or {}).get("teams") if isinstance(matchup, dict) else None, "team"):
            meta, stats = merge(team[0] if team and isinstance(team[0], list) else team), {}
            if isinstance(team, list) and len(team) > 1 and isinstance(team[1], dict):
                stats = team[1]
            teams.append({"team_key": meta.get("team_key"), "name": meta.get("name"),
                          "points": _float((stats.get("team_points") or {}).get("total")),
                          "projected": _float((stats.get("team_projected_points") or {}).get("total")),
                          "win_probability": _float(stats.get("win_probability"))})
        if teams:
            out.append({"status": matchup.get("status") if isinstance(matchup, dict) else None, "teams": teams})
    return out


def league_team_keys(teams: Iterable[dict]) -> list[str]:
    return [t["team_key"] for t in teams if t.get("team_key")]


def parse_standings(content: dict) -> list[dict]:
    """Each team's record, points for/against, playoff seed and activity from /league/{key}/standings."""
    league = content.get("league") or []
    standings = merge(league[1:]).get("standings") if len(league) > 1 else None
    first = standings[0] if isinstance(standings, list) and standings else standings
    out = []
    for team in collection((first or {}).get("teams") if isinstance(first, dict) else None, "team"):
        info = merge(team)
        record = info.get("team_standings") or {}
        totals = record.get("outcome_totals") or {}
        out.append({"team_key": info.get("team_key"), "name": info.get("name"),
                    "rank": _number(record.get("rank"), int) or None,
                    "wins": _number(totals.get("wins"), int), "losses": _number(totals.get("losses"), int),
                    "ties": _number(totals.get("ties"), int),
                    "points_for": _float(record.get("points_for")) or 0.0,
                    "points_against": _float(record.get("points_against")) or 0.0,
                    "moves": _number(info.get("number_of_moves"), int),
                    "trades": _number(info.get("number_of_trades"), int),
                    "faab_balance": _float(info.get("faab_balance")),
                    "waiver_priority": _number(info.get("waiver_priority"), int) or None})
    return out


def parse_transactions(content: dict) -> list[dict]:
    """Adds, drops and trades from /league/{key}/transactions: who moved which player where."""
    league = content.get("league") or []
    container = merge(league[1:]).get("transactions") if len(league) > 1 else None
    out = []
    for transaction in collection(container, "transaction"):
        if not isinstance(transaction, list) or not transaction:
            continue
        meta = merge(transaction[0] if isinstance(transaction[0], (list, dict)) else {})
        players_box = merge(transaction[1:]).get("players")
        moves = []
        for player in collection(players_box, "player"):
            info = merge(player[0] if isinstance(player, list) and player and isinstance(player[0], list) else player)
            data = merge(player[1:]).get("transaction_data") if isinstance(player, list) else None
            data = data[0] if isinstance(data, list) and data else data
            if not isinstance(data, dict):
                continue
            name = (info.get("name") or {}).get("full") if isinstance(info.get("name"), dict) else info.get("name")
            moves.append({"yahoo_player_id": str(info.get("player_id")) if info.get("player_id") else None,
                          "name": name, "position": info.get("display_position"), "type": data.get("type"),
                          "from_team": data.get("source_team_key"), "to_team": data.get("destination_team_key"),
                          "faab_bid": _float(meta.get("faab_bid"))})
        out.append({"type": meta.get("type"), "status": meta.get("status"),
                    "timestamp": _number(meta.get("timestamp"), int), "moves": moves})
    return out
