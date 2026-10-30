"""Vegas player props from The Odds API (https://the-odds-api.com, needs ODDS_API_KEY).

The free plan gives 500 credits a month. Listing events is free; each game's props cost
(markets x regions) = 7 credits. Each game's props are bought ONCE, inside PROPS_WINDOW_HOURS
(2) before kickoff, after Sunday-morning injury news and close to inactives (the auto-update
checks every 15 minutes on game days). That is about 16 x 7 = ~112 credits a week; when the
month's remaining credits can't cover every game left, only games with a player on one of
your rosters (and every one-game Thursday/Monday slate) are bought. Buying a game again needs
an explicit force (CLI --force-props).

- Every purchase is logged per event in external_api_calls, so the budget survives
  restarts. A game that came back with no props posted yet is retried at most every
  EMPTY_RETRY_HOURS.
- Each game's rows are stored as soon as they're bought: a failure partway through keeps
  everything already paid for.
- A refresh stops before the remaining-credits header would drop below CREDIT_RESERVE.
- After a failed call, refreshes back off for FAILURE_BACKOFF_MINUTES.
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import delete, func, insert, select
from sqlalchemy.orm import Session

from database.models import NflGame, WeeklySourceProjection
from integrations.weekly_sources import (
    FAILURE_BACKOFF_MINUTES, PlayerMatcher, SourceError, in_backoff, last_call, record_call, source_row,
)
from services import props_math

log = logging.getLogger(__name__)

BASE_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl"
SOURCE = "vegas_props"
CALL_LOG_SOURCE = "odds_api"
REGIONS = "us"
PROPS_WINDOW_HOURS = 2           # close to kickoff: the lines know Sunday's news and inactives
ONE_GAME_SLATE_DAYS = (0, 3)     # Monday, Thursday (datetime.weekday): always bought
EMPTY_RETRY_HOURS = 6
CREDIT_RESERVE = 10
PARSE_ERRORS = (KeyError, TypeError, ValueError, AttributeError)
# Odds API market key -> (projected-stat key, how to read it)
MARKETS = {
    "player_pass_yds": ("pass_yds", "yards"),
    "player_pass_tds": ("pass_tds", "count"),
    "player_pass_interceptions": ("ints", "count"),
    "player_rush_yds": ("rush_yds", "yards"),
    "player_receptions": ("rec", "count"),
    "player_reception_yds": ("rec_yds", "yards"),
    "player_anytime_td": ("scrimmage_tds", "anytime"),
}
CREDITS_PER_GAME = len(MARKETS) * len(REGIONS.split(","))
TEAM_NAMES = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LA", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


class OddsApiClient:
    def __init__(self, api_key: str, http: httpx.Client):
        if not api_key:
            raise SourceError("ODDS_API_KEY is not set in backend/.env.")
        self._api_key = api_key
        self._http = http
        self.remaining: int | None = None

    def _get(self, path: str, params: dict | None = None):
        try:
            response = self._http.get(f"{BASE_URL}{path}", params={"apiKey": self._api_key, **(params or {})})
        except httpx.HTTPError as exc:
            raise SourceError(f"The Odds API request failed ({type(exc).__name__}).") from exc
        if response.status_code == 401:
            raise SourceError("The Odds API rejected the key (check ODDS_API_KEY).")
        if response.status_code == 429:
            raise SourceError("The Odds API quota or rate limit was hit; try again later.")
        if response.status_code >= 400:
            raise SourceError(f"The Odds API returned HTTP {response.status_code}.")
        try:
            self.remaining = int(float(response.headers["x-requests-remaining"]))
        except (KeyError, ValueError):
            pass                                   # header missing or odd: keep the last value
        try:
            return response.json()
        except ValueError as exc:
            raise SourceError("The Odds API returned a non-JSON response.") from exc

    def events(self) -> list[dict]:
        events = self._get("/events")
        if not isinstance(events, list) or not all(isinstance(e, dict) for e in events):
            raise SourceError("The Odds API returned an unexpected events list.")
        return events

    def event_props(self, event_id: str) -> dict:
        props = self._get(f"/events/{event_id}/odds", {"regions": REGIONS, "markets": ",".join(MARKETS),
                                                       "oddsFormat": "american"})
        if not isinstance(props, dict):
            raise SourceError("The Odds API returned unexpected props.")
        return props


def _book_estimate(kind: str, stat: str, outcomes: list[dict]) -> float | None:
    """One bookmaker's expected value for one player and market."""
    sides = {o.get("name"): o for o in outcomes}
    if kind == "anytime":
        yes, no = sides.get("Yes"), sides.get("No")
        return props_math.anytime_td_mean(yes["price"], no["price"] if no else None) if yes else None
    over, under = sides.get("Over"), sides.get("Under")
    if not over or not under or over.get("point") is None:
        return None
    probability = props_math.fair_probability(over["price"], under["price"])
    if kind == "count":
        return props_math.poisson_mean(float(over["point"]), probability)
    return props_math.yardage_mean(stat, float(over["point"]), probability)


def expected_stats(payload: dict) -> dict[str, dict[str, float]]:
    """{player name: {stat: expected value}}, averaged over every bookmaker quoting it."""
    estimates: dict[str, dict[str, list[float]]] = {}
    for book in payload.get("bookmakers") or []:
        for market in book.get("markets") or []:
            if market.get("key") not in MARKETS:
                continue
            stat, kind = MARKETS[market["key"]]
            by_player: dict[str, list[dict]] = {}
            for outcome in market.get("outcomes") or []:
                if outcome.get("description"):
                    by_player.setdefault(outcome["description"], []).append(outcome)
            for name, outcomes in by_player.items():
                value = _book_estimate(kind, stat, outcomes)
                if value is not None:
                    estimates.setdefault(name, {}).setdefault(stat, []).append(value)
    return {name: {stat: sum(v) / len(v) for stat, v in stats.items()} for name, stats in estimates.items()}


def prop_rows(payload: dict, game: NflGame, season: int, week: int, matcher: PlayerMatcher) -> list[dict]:
    rows = []
    teams = (game.home_team, game.away_team)
    for name, stats in expected_stats(payload).items():
        player_id = matcher.match(name, teams=teams)
        team = matcher.team.get(player_id)
        if player_id is None or team not in teams:   # unmatched, or our roster data is stale
            continue
        opponent = teams[1] if team == teams[0] else teams[0]
        rows.append(source_row(season, week, SOURCE, player_id=player_id, name=name,
                               position=matcher.position.get(player_id), team=team,
                               opponent=opponent, stats=stats))
    return rows


def _upcoming_games(session: Session, season: int, week: int) -> dict[tuple[str, str], NflGame]:
    games = session.scalars(select(NflGame).where(NflGame.season == season, NflGame.week == week,
                                                  NflGame.home_score.is_(None))).all()
    # Keyed both ways: neutral-site games may list home/away differently than nflverse.
    keyed = {(g.home_team, g.away_team): g for g in games}
    return {**{(a, h): g for (h, a), g in keyed.items()}, **keyed}


def _event_log(event_id: str) -> str:
    return f"{CALL_LOG_SOURCE}:event:{event_id}"


def _stored_props(session: Session, season: int, week: int, game: NflGame) -> int:
    return session.scalar(select(func.count(WeeklySourceProjection.id)).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == SOURCE,
        WeeklySourceProjection.team.in_((game.home_team, game.away_team)))) or 0


def _is_due(session: Session, event_id: str, game: NflGame, season: int, week: int, now: datetime) -> bool:
    """Not bought yet, or bought while no props were posted and EMPTY_RETRY_HOURS have passed."""
    latest = last_call(session, _event_log(event_id))
    if latest is None:
        return True
    if latest[1] and _stored_props(session, season, week, game):
        return False                               # already paid for
    return now - latest[0] >= timedelta(hours=EMPTY_RETRY_HOURS)


def _due_games(session: Session, events: list[dict], games: dict, season: int, week: int,
               force: bool) -> list[tuple[str, NflGame]]:
    now = datetime.now(timezone.utc)
    due = []
    for event in events:
        key = (TEAM_NAMES.get(event.get("home_team")), TEAM_NAMES.get(event.get("away_team")))
        if key not in games or not event.get("id") or not event.get("commence_time"):
            continue
        try:
            kickoff = datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if not now < kickoff <= now + timedelta(hours=PROPS_WINDOW_HOURS):
            continue
        if force or _is_due(session, event["id"], games[key], season, week, now):
            due.append((event["id"], games[key]))
    return due


def _always_buy(game: NflGame) -> bool:
    try:
        return datetime.strptime(game.gameday or "", "%Y-%m-%d").weekday() in ONE_GAME_SLATE_DAYS
    except ValueError:
        return False


def credits_needed(session: Session, season: int, now: datetime | None = None) -> int:
    """Credits to buy every unplayed game left this calendar month."""
    now = now or datetime.now(timezone.utc)
    month_end = (now.replace(day=28) + timedelta(days=4)).replace(day=1).strftime("%Y-%m-%d")
    games = session.scalar(select(func.count(NflGame.game_id)).where(
        NflGame.season == season, NflGame.home_score.is_(None), NflGame.gameday >= now.strftime("%Y-%m-%d"),
        NflGame.gameday < month_end))
    return (games or 0) * CREDITS_PER_GAME


def _store_game(session: Session, rows: list[dict], game: NflGame, season: int, week: int) -> None:
    session.execute(delete(WeeklySourceProjection).where(
        WeeklySourceProjection.season == season, WeeklySourceProjection.week == week,
        WeeklySourceProjection.source == SOURCE,
        WeeklySourceProjection.team.in_((game.home_team, game.away_team))))
    if rows:
        session.execute(insert(WeeklySourceProjection), rows)


def _buy_game(session: Session, client: OddsApiClient, event_id: str, game: NflGame,
              matcher: PlayerMatcher, season: int, week: int) -> int:
    """Fetch, store and log one game's props; returns rows stored."""
    try:
        rows = prop_rows(client.event_props(event_id), game, season, week, matcher)
    except PARSE_ERRORS as exc:
        raise SourceError(f"The Odds API returned unexpected props ({type(exc).__name__}).") from exc
    _store_game(session, rows, game, season, week)
    session.commit()
    record_call(session, _event_log(event_id), ok=True)
    return len(rows)


def refresh(session: Session, client: OddsApiClient, season: int, week: int, force: bool = False,
            only_teams: set[str] | None = None) -> int:
    """Buy props for the week's games that are due (see module docstring). Returns games bought.

    Raises SourceError (keeping every game already stored) on API errors, when credits
    run low, or while backing off after a failure.
    """
    if in_backoff(session, CALL_LOG_SOURCE):
        raise SourceError(f"the last Odds API call failed; retrying after {FAILURE_BACKOFF_MINUTES} min")
    games = _upcoming_games(session, season, week)
    if not games:
        raise SourceError(f"no upcoming week {week} games in the schedule (run populate_stats)")
    matcher = PlayerMatcher(session)
    bought = 0
    try:
        for event_id, game in _due_games(session, client.events(), games, season, week, force):
            if only_teams is not None and not _always_buy(game) and not {game.home_team, game.away_team} & only_teams:
                continue                               # credits are short: rostered games only
            if client.remaining is not None and client.remaining < CREDITS_PER_GAME + CREDIT_RESERVE:
                raise SourceError(f"Odds API credits are nearly used up ({client.remaining} left); "
                                  "remaining games wait for the monthly reset")
            _buy_game(session, client, event_id, game, matcher, season, week)
            bought += 1
    except SourceError as exc:
        session.rollback()
        record_call(session, CALL_LOG_SOURCE, ok=False)
        suffix = f" ({bought} games stored before it stopped)" if bought else ""
        raise SourceError(f"{exc}{suffix}") from exc
    record_call(session, CALL_LOG_SOURCE, ok=True)
    return bought
