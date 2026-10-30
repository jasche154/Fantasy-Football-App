"""Yahoo connection storage (encrypted tokens) and league/roster import.

The app is single-user for now, so the connection belongs to one local user row. Rosters
are matched to local players through players.yahoo_player_id (from the nflverse crosswalk).
"""
import json
import threading
import time
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from config import Settings
from database.models import League, PlatformConnection, Player, Roster, Team, User
from integrations import yahoo
from integrations.weekly_sources import PlayerMatcher
from services.league import POSITIONS
from integrations.token_crypto import decrypt, encrypt, require_valid_key, sign_state, state_is_valid

PLATFORM = "yahoo"
LOCAL_USER_EMAIL = "local-user@localhost"
OUT_OF_BAND = "oob"
STATE_TTL_SECONDS = 15 * 60
STATE_PURPOSE = "yahoo-oauth-state"


class YahooNotConfigured(RuntimeError):
    pass


def require_config(settings: Settings) -> None:
    """App credentials and a usable encryption key must exist before any OAuth step."""
    if not (settings.yahoo_client_id and settings.yahoo_client_secret):
        raise YahooNotConfigured("Set YAHOO_CLIENT_ID and YAHOO_CLIENT_SECRET in backend/.env.")
    require_valid_key(settings.token_encryption_key)


def start_authorization(settings: Settings) -> str:
    require_config(settings)
    state = sign_state(STATE_PURPOSE, settings.token_encryption_key)
    return yahoo.authorization_url(settings.yahoo_client_id, settings.yahoo_redirect_uri, state)


def check_state(state: str | None, settings: Settings) -> None:
    """The pasted redirect URL must carry a state this app signed in the last 15 minutes.

    Only the out-of-band flow, where Yahoo shows a bare code, can skip it.
    """
    if state is None:
        if settings.yahoo_redirect_uri == OUT_OF_BAND:
            return
        raise yahoo.YahooAuthError("Paste the whole redirected URL (it includes a security check), "
                                   "not just the code.")
    if not state_is_valid(state, STATE_PURPOSE, settings.token_encryption_key, STATE_TTL_SECONDS):
        raise yahoo.YahooAuthError("This authorization link is expired or wasn't created by this app; "
                                   "click Connect Yahoo again.")


def local_user(session: Session) -> User:
    user = session.scalar(select(User).where(User.email == LOCAL_USER_EMAIL))
    if user is None:
        user = User(email=LOCAL_USER_EMAIL, hashed_password="!")  # "!" = no password login
        session.add(user)
        session.flush()
    return user


def get_connection(session: Session) -> PlatformConnection | None:
    return session.scalar(select(PlatformConnection).join(User).where(
        User.email == LOCAL_USER_EMAIL, PlatformConnection.platform == PLATFORM))


def save_token(session: Session, settings: Settings, token: yahoo.YahooToken) -> PlatformConnection:
    connection = get_connection(session) or PlatformConnection(user_id=local_user(session).id, platform=PLATFORM)
    connection.access_token = encrypt(token.access_token, settings.token_encryption_key)
    connection.refresh_token = encrypt(token.refresh_token, settings.token_encryption_key)
    connection.token_expires_at = datetime.fromtimestamp(token.expires_at, tz=timezone.utc)
    session.add(connection)
    session.commit()
    return connection


def load_token(connection: PlatformConnection, settings: Settings) -> yahoo.YahooToken:
    expires = connection.token_expires_at
    if expires is not None and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return yahoo.YahooToken(
        access_token=decrypt(connection.access_token, settings.token_encryption_key) or "",
        refresh_token=decrypt(connection.refresh_token, settings.token_encryption_key),
        expires_at=expires.timestamp() if expires else 0.0)


def connect(session: Session, settings: Settings, http: httpx.Client, code: str, state: str | None) -> None:
    require_config(settings)
    check_state(state, settings)
    token = yahoo.exchange_code(http, settings.yahoo_client_id, settings.yahoo_client_secret,
                                settings.yahoo_redirect_uri, code)
    save_token(session, settings, token)


def fresh_token(session: Session, settings: Settings, http: httpx.Client) -> yahoo.YahooToken:
    connection = get_connection(session)
    if connection is None or connection.access_token is None:
        raise yahoo.YahooAuthError("Yahoo isn't connected yet.")
    token = load_token(connection, settings)
    if token.is_expired():
        token = yahoo.refresh_token(http, settings.yahoo_client_id, settings.yahoo_client_secret,
                                    settings.yahoo_redirect_uri, token)
        save_token(session, settings, token)
    return token


def list_leagues(session: Session, settings: Settings, http: httpx.Client) -> list[dict]:
    token = fresh_token(session, settings, http)
    return yahoo.parse_leagues(yahoo.api_get(http, token, "users;use_login=1/games;game_keys=nfl/leagues"))


def _upsert_league(session: Session, connection: PlatformConnection, meta: dict, settings_json: dict) -> League:
    league = session.scalar(select(League).where(League.platform_connection_id == connection.id,
                                                 League.platform_league_id == meta["league_key"]))
    league = league or League(platform_connection_id=connection.id, platform_league_id=meta["league_key"])
    league.name = meta.get("name") or meta["league_key"]
    league.season = meta.get("season") or 0
    league.scoring_settings = json.dumps(settings_json)
    session.add(league)
    session.flush()
    return league


def _upsert_team(session: Session, league: League, meta: dict) -> Team:
    team = session.scalar(select(Team).where(Team.league_id == league.id,
                                             Team.platform_team_id == meta["team_key"]))
    team = team or Team(league_id=league.id, platform_team_id=meta["team_key"])
    team.name, team.owner_display_name = meta.get("name"), meta.get("owner")
    team.is_users_team = meta.get("is_owned_by_current_login", False)
    session.add(team)
    session.flush()
    return team


_import_lock = threading.Lock()


def import_league(session: Session, settings: Settings, http: httpx.Client, league_key: str) -> dict:
    """One import at a time: a manual sync and the background re-sync rewrite the same roster rows."""
    with _import_lock:
        return _import_league(session, settings, http, league_key)


def modeled_position(display_position: str | None) -> str | None:
    """'RB' -> 'RB'; Yahoo's dual eligibility 'RB,WR' -> 'RB' (the first modeled one); 'K' -> 'K'; 'DEF' -> 'DST'."""
    parts = [p.strip() for p in (display_position or "").split(",")]
    for part in parts:
        if part in POSITIONS:
            return part
    if "K" in parts:
        return "K"
    if "DEF" in parts:
        return "DST"
    return None


def _match_special(session: Session, entry: dict, position: str) -> int | None:
    """Kickers by name (+ team), defenses by team: the app's K/DST models cover them."""
    from integrations.weekly_sources import normalize_name, normalize_team
    from services.kdst_rankings import dst_player

    team = normalize_team(entry.get("team"))
    if position == "DST":
        return dst_player(session, team).id if team else None
    wanted = normalize_name(entry.get("name") or "")
    kickers = [p for p in session.scalars(select(Player).where(Player.position == "K"))
               if normalize_name(p.full_name or "") == wanted]
    if len(kickers) > 1 and team:
        kickers = [p for p in kickers if p.nfl_team == team] or kickers
    return kickers[0].id if len(kickers) == 1 else None


def _import_league(session: Session, settings: Settings, http: httpx.Client, league_key: str) -> dict:
    token = fresh_token(session, settings, http)
    leagues = {l["league_key"]: l for l in list_leagues(session, settings, http)}
    meta = leagues.get(league_key)
    if meta is None:
        raise LookupError(f"League {league_key} isn't one of your Yahoo NFL leagues.")
    week = meta.get("current_week") or 1
    rules = yahoo.parse_settings(yahoo.api_get(http, token, f"league/{league_key}/settings"))
    league = _upsert_league(session, get_connection(session), meta,
                            {"scoring_type": meta.get("scoring_type"), "num_teams": meta.get("num_teams"),
                             "week": week, **rules})
    matcher = PlayerMatcher(session)
    teams = yahoo.parse_teams(yahoo.api_get(http, token, f"league/{league_key}/teams"))
    spots, unmatched, untracked = 0, [], []
    for team_meta in teams:
        team = _upsert_team(session, league, team_meta)
        roster = yahoo.parse_roster(yahoo.api_get(http, token, f"team/{team_meta['team_key']}/roster;week={week}"))
        session.execute(delete(Roster).where(Roster.team_id == team.id, Roster.week == week))
        for entry in roster:
            name = entry["name"] or entry["yahoo_player_id"] or "unknown"
            position = modeled_position(entry.get("position"))
            if position is None:
                untracked.append(name)                     # team defenses and kickers aren't modeled
                continue
            player_id = (_match_special(session, entry, position) if position in ("K", "DST")
                         else _match_roster_player(session, matcher, {**entry, "position": position}))
            if player_id is None:
                unmatched.append(name)
                continue
            session.add(Roster(team_id=team.id, player_id=player_id, week=week, roster_slot=entry["slot"] or "BN"))
            spots += 1
    # Kept so league waivers never offer a rostered player the import couldn't match.
    league.scoring_settings = json.dumps({**json.loads(league.scoring_settings or "{}"), "unmatched": unmatched})
    session.commit()
    return {"league_id": league.id, "teams": len(teams), "roster_spots": spots, "unmatched_players": unmatched,
            "untracked_players": untracked}


def _match_roster_player(session: Session, matcher: PlayerMatcher, entry: dict) -> int | None:
    """Yahoo id first; otherwise a name (+ position, team) match.

    The Yahoo id is saved only on a strict match: the one player with that name, position
    and team. A looser match (e.g. a same-named player on another team) is used for this
    import but never saved, so a wrong guess can't stick to the wrong player for good.
    """
    yahoo_id = entry.get("yahoo_player_id")
    player_id = matcher.match(entry.get("name") or "", team=entry.get("team"), position=entry.get("position"),
                              yahoo_id=yahoo_id)
    if player_id is None or not yahoo_id or yahoo_id in matcher.by_yahoo:
        return player_id
    strict = matcher.strict_match(entry.get("name") or "", entry.get("team"), entry.get("position"))
    if strict == player_id:
        player = session.get(Player, player_id)
        if player is not None and not player.yahoo_player_id:
            player.yahoo_player_id = yahoo_id              # the crosswalk lacks recent rookies
            matcher.by_yahoo[yahoo_id] = player_id
    return player_id
