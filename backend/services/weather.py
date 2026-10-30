"""Kickoff weather forecasts for upcoming outdoor games (Open-Meteo, free, no key).

nflverse fills temperature and wind only after a game. For games within FORECAST_DAYS, the
forecast at the home stadium for the kickoff hour is stored in game_forecasts and used by
the projection factors and the kicker model. Dome / closed-roof games are skipped. Games
at neutral sites (London, Germany, ...) are skipped too: the home team's stadium isn't
where they're played.
"""
import logging
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import GameForecast, NflGame
from services.game_features import INDOORS, Conditions

log = logging.getLogger(__name__)
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
FORECAST_DAYS = 7
REFRESH_HOURS = 6
# Home stadium coordinates (lat, lon), by nflverse team abbreviation.
STADIUMS = {
    "ARI": (33.528, -112.263), "ATL": (33.755, -84.401), "BAL": (39.278, -76.623), "BUF": (42.774, -78.787),
    "CAR": (35.226, -80.853), "CHI": (41.862, -87.617), "CIN": (39.095, -84.516), "CLE": (41.506, -81.700),
    "DAL": (32.748, -97.093), "DEN": (39.744, -105.020), "DET": (42.340, -83.046), "GB": (44.501, -88.062),
    "HOU": (29.685, -95.411), "IND": (39.760, -86.164), "JAX": (30.324, -81.637), "KC": (39.049, -94.484),
    "LA": (33.953, -118.339), "LAC": (33.953, -118.339), "LV": (36.091, -115.184), "MIA": (25.958, -80.239),
    "MIN": (44.974, -93.258), "NE": (42.091, -71.264), "NO": (29.951, -90.081), "NYG": (40.814, -74.074),
    "NYJ": (40.814, -74.074), "PHI": (39.901, -75.168), "PIT": (40.447, -80.016), "SEA": (47.595, -122.332),
    "SF": (37.403, -121.970), "TB": (27.976, -82.503), "TEN": (36.166, -86.771), "WAS": (38.908, -76.865),
}


def _kickoff_hour(game: NflGame) -> int:
    try:
        return int((game.gametime or "13:00").split(":")[0])
    except ValueError:
        return 13


def _usual_stadiums(games) -> dict[str, str]:
    """Each team's most common home stadium this season; a game elsewhere is at a neutral site."""
    counts: dict[str, Counter] = defaultdict(Counter)
    for g in games:
        if g.stadium_id:
            counts[g.home_team][g.stadium_id] += 1
    return {team: c.most_common(1)[0][0] for team, c in counts.items()}


def due_games(session: Session, season: int, week: int, today: date | None = None) -> list[NflGame]:
    today = today or date.today()
    season_games = session.scalars(select(NflGame).where(NflGame.season == season)).all()
    usual = _usual_stadiums(season_games)
    return [g for g in season_games if g.week == week and g.home_score is None and g.gameday
            and g.home_team in STADIUMS and (g.roof or "").lower() not in INDOORS
            and (not g.stadium_id or usual.get(g.home_team) == g.stadium_id)
            and 0 <= (date.fromisoformat(g.gameday) - today).days <= FORECAST_DAYS]


def fetch_forecast(http: httpx.Client, game: NflGame) -> tuple[float, float] | None:
    """(temp F, wind mph) at the kickoff hour, US Eastern; None if Open-Meteo has no answer."""
    lat, lon = STADIUMS[game.home_team]
    response = http.get(FORECAST_URL, params={
        "latitude": lat, "longitude": lon, "hourly": "temperature_2m,wind_speed_10m",
        "temperature_unit": "fahrenheit", "wind_speed_unit": "mph", "timezone": "America/New_York",
        "start_date": game.gameday, "end_date": game.gameday})
    if response.status_code != 200:
        return None
    hourly = response.json().get("hourly") or {}
    temps, winds = hourly.get("temperature_2m") or [], hourly.get("wind_speed_10m") or []
    hour = _kickoff_hour(game)
    if len(temps) <= hour or len(winds) <= hour or temps[hour] is None or winds[hour] is None:
        return None
    return float(temps[hour]), float(winds[hour])


def refresh_forecasts(session: Session, http: httpx.Client, season: int, week: int, force: bool = False) -> int:
    """Fetch forecasts for the week's upcoming outdoor games; returns how many were stored."""
    now = datetime.now(timezone.utc)
    stored = 0
    for game in due_games(session, season, week):
        row = session.get(GameForecast, game.game_id)
        fetched = row.fetched_at.replace(tzinfo=timezone.utc) if row and row.fetched_at.tzinfo is None else (
            row.fetched_at if row else None)
        if row and not force and fetched and now - fetched < timedelta(hours=REFRESH_HOURS):
            continue
        try:
            forecast = fetch_forecast(http, game)
        except httpx.HTTPError as exc:
            log.warning("Weather forecast failed for %s (%s)", game.game_id, type(exc).__name__)
            continue
        if forecast is None:
            continue
        row = row or GameForecast(game_id=game.game_id)
        row.temp, row.wind, row.fetched_at = forecast[0], forecast[1], now
        session.add(row)
        stored += 1
    session.commit()
    return stored


def forecasts(session: Session, game_ids) -> dict[str, Conditions]:
    ids = list(game_ids)
    if not ids:
        return {}
    rows = session.scalars(select(GameForecast).where(GameForecast.game_id.in_(ids)))
    return {r.game_id: Conditions(False, r.temp, r.wind) for r in rows}
