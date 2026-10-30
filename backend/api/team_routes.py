"""Your imported Yahoo teams: roster and lineup, league-aware waivers and trades."""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from api.deps import Position
from api.integration_routes import HTTP_TIMEOUT_SECONDS, YAHOO_ERRORS, _yahoo_http_error
from api.weekly_routes import flags_for
from config import get_settings
from database.db import get_session
from database.models import Player
from services import my_team as mt
from services import serialize, yahoo_sync
from services.valuation import NoDataError, build_valuation

router = APIRouter(prefix="/api/my-team")


def _league_or_404(session: Session, league_id: int) -> tuple:
    try:
        return mt.find_league(session, league_id)
    except mt.LeagueNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


def _league_summary(league, team) -> dict:
    rules = mt.league_rules(league)
    return {"id": league.id, "key": league.platform_league_id, "name": league.name, "season": league.season,
            "team": team.name, "scoring": rules.get("scoring", "ppr"), "teams": rules.get("num_teams"),
            "starters": rules.get("starters"), "week": rules.get("week")}


def _plain_ref(player_id: int, player: Player | None) -> dict:
    return {"id": player_id, "name": player.full_name if player else f"Player {player_id}",
            "position": player.position if player else None, "team": player.nfl_team if player else None}


@router.get("/leagues")
def leagues(session: Session = Depends(get_session)) -> dict:
    return {"leagues": [_league_summary(league, team) for league, team in mt.my_leagues(session)]}


@router.get("/{league_id}")
def my_team(league_id: int, session: Session = Depends(get_session)) -> dict:
    _league_or_404(session, league_id)
    try:
        team = mt.build_my_team(session, league_id)
    except NoDataError as exc:
        raise HTTPException(503, str(exc)) from exc
    by_id = team.weekly()
    flags = flags_for(team.rankings, team.valuation)
    # Rostered players with no stats this season (a rookie yet to play) still need a name.
    unknown = {s.player_id for s in team.spots if s.player_id not in team.valuation.by_id}
    people = {p.id: p for p in session.scalars(select(Player).where(Player.id.in_(unknown)))} if unknown else {}
    best_ids = {pid for ids in team.best.values() for pid in ids}
    roster = []
    for spot in team.spots:
        value = team.valuation.by_id.get(spot.player_id)
        weekly = by_id.get(spot.player_id)
        roster.append({
            "slot": spot.slot, "starting": spot.starting, "in_best_lineup": spot.player_id in best_ids,
            "player": serialize.player_ref(value) if value else _plain_ref(spot.player_id, people.get(spot.player_id)),
            "week": serialize.weekly_player(weekly, flags.get(spot.player_id, ())) if weekly else None,
            "ros_ppg": value.ros_ppg if value else None, "trade_value": value.trade_value if value else None,
            "pos_rank": value.pos_rank if value else None,
        })
    starting_ids = [s.player_id for s in team.spots if s.starting]
    return {
        "league": _league_summary(team.league, team.team), "week": team.rankings.week,
        "roster": roster,
        "lineup": {
            "current_total": mt.lineup_total(starting_ids, by_id),
            "best_total": mt.lineup_total([pid for ids in team.best.values() for pid in ids], by_id),
            "changes": [{"start": serialize.player_ref(c.start), "start_projection": c.start.projection,
                         "bench": serialize.player_ref(team.valuation.by_id[c.bench_id])
                         if c.bench_id in team.valuation.by_id else None,
                         "bench_projection": c.bench_projection if c.bench_id is not None else None,
                         "slot": c.slot, "gain": c.gain} for c in team.changes],
        },
    }


@router.get("/{league_id}/waivers")
def waivers(league_id: int, position: Position | None = None, limit: int = Query(25, ge=1, le=100),
            session: Session = Depends(get_session)) -> dict:
    _league_or_404(session, league_id)
    league, rankings, candidates = mt.league_waivers(session, league_id, position, limit)
    return {"league": league.name, "season": rankings.season, "week": rankings.week,
            "results": [serialize.waiver(c) for c in candidates]}


@router.get("/{league_id}/trades")
def trades(league_id: int, want_position: Position | None = None, session: Session = Depends(get_session)) -> dict:
    _league_or_404(session, league_id)
    league, by_team = mt.league_trades(session, league_id, want_position)
    weeks = build_valuation(session, mt.league_settings(league)).weeks_remaining
    return {"league": league.name, "perspective": "give = from your roster, get = from their roster",
            "teams": [{"team": r.team.name, "owner": r.team.owner_display_name, "needs": list(r.needs),
                       "offers": [{**serialize.trade_offer(o, weeks),
                                   "fills_their_need": [p.position for p in o.give if p.position in r.needs]}
                                  for o in r.offers]} for r in by_team]}


@router.post("/{league_id}/sync")
def sync(league_id: int, session: Session = Depends(get_session)) -> dict:
    """Re-import this league's rosters and rules from Yahoo."""
    league, _ = _league_or_404(session, league_id)
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            return yahoo_sync.import_league(session, get_settings(), http, league.platform_league_id)
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc


@router.get("/{league_id}/matchup")
def matchup(league_id: int, session: Session = Depends(get_session)) -> dict:
    """This week's opponent, both lineups, win probability, and the upside / safe / expected lineups."""
    from services.matchup import build_matchup

    _league_or_404(session, league_id)
    try:
        with httpx.Client(timeout=HTTP_TIMEOUT_SECONDS) as http:
            return build_matchup(session, get_settings(), http, league_id)
    except YAHOO_ERRORS as exc:
        raise _yahoo_http_error(exc) from exc


@router.get("/{league_id}/playoffs")
def playoffs(league_id: int, session: Session = Depends(get_session)) -> dict:
    """Each player's fantasy-playoff weeks (opponent, schedule factor, bye, projection) and the best lineup per week."""
    from services.playoffs import playoff_outlook

    _league_or_404(session, league_id)
    return playoff_outlook(session, league_id)
