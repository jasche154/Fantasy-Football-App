"""Replay League: seasons, saved games and every screen of a game (the user's actions are in
api/replay_action_routes.py). Response shapes are documented at the top of services/replay_views.py.

Every change to a game runs load -> engine -> save under the replay lock (`turn`, `game_lock`), so two
overlapping requests can never lose a write or play a week twice. The lock is never evicted (unlike
build_locks.key_lock, whose bounded dict can hand two requests different locks for one key).
"""
import logging
import secrets
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.deps import Scoring
from database.db import get_session
from database.models import ReplayLeague
from services import (
    replay_adp_format, replay_blob, replay_data, replay_game as rg, replay_live, replay_pool, replay_scoring, replay_slots,
    replay_state as rs, replay_views as views, replay_views_league as league_views, sim_setup,
)
from services.replay_pool import SeasonData
from services.replay_state import Settings, State
from services.valuation import NoDataError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/replay")

MAX_SEED = 2 ** 31 - 1
MAX_NAME_LENGTH = 60
Limit = Query(views.DEFAULT_PAGE, ge=1, le=views.MAX_PAGE)
Offset = Query(0, ge=0)
Week = Query(None, ge=0, le=22)
TeamId = Query(None, ge=1)
GameId = Annotated[int, Path(ge=1, le=2 ** 63 - 1)]      # SQLite INTEGER range: a bigger id is a 422, not a 500

_GAME_LOCK = threading.Lock()


def game_lock(game_id: int) -> threading.Lock:
    """The lock a change to a replay game runs under. shortcut: one lock for every game (single-user app);
    key it by game id (in a dict that never evicts) if games ever need to change in parallel."""
    return _GAME_LOCK


@contextmanager
def errors() -> Iterator[None]:
    """Domain errors as HTTP: an unknown game or player 404, season data not loaded 503, bad input 422, a saved
    season that can't be decoded 500 (the server's fault, with the reason; GET /games still lists the league, so
    it can be deleted)."""
    try:
        yield
    except rs.ReplayNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except replay_blob.UnreadableSeason as exc:
        raise HTTPException(500, f"{exc} Delete this league to start another.") from exc
    except NoDataError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def _season_data(session: Session, settings: Settings, league_id: int) -> SeasonData:
    """The season as this league sees it: a simulated league's frozen season (replay_blob), else the past season
    with its scoring and its ADP list (replay_adp_format)."""
    if settings.sim_from is not None:
        return replay_blob.load(session, league_id)
    return replay_pool.load_season_data(session, settings.season, settings.scoring, settings.scoring_rules,
                                        settings.adp_format)


def _game(row: ReplayLeague, settings: Settings, state: State, data: SeasonData) -> views.Game:
    return views.Game(row.id, row.name, settings, state, data, row.updated_at.isoformat(), row.seed)


def open_game(session: Session, game_id: int) -> views.Game:
    row, settings, state = rs.load(session, game_id)
    return _game(row, settings, state, _season_data(session, settings, row.id))


def show(session: Session, game_id: int, build: Callable[[views.Game], dict]) -> dict:
    with errors():
        return build(open_game(session, game_id))


@dataclass(frozen=True)
class Turn:
    """One change to a game, in progress under its lock."""
    session: Session
    row: ReplayLeague
    settings: Settings
    state: State
    data: SeasonData                        # the full season: for the engine only

    @property
    def args(self) -> tuple[State, Settings, SeasonData]:
        return self.state, self.settings, self.data

    @property
    def seed(self) -> int:
        return self.row.seed

    def done(self, state: State, **extras) -> dict:
        """Save the new state and answer {game, events, ...extras}; a callable extra gets the new Game."""
        if state is not self.state:
            rs.save(self.session, self.row, state)
        game = _game(self.row, self.settings, state, self.data)
        return {"game": views.summary(game), "events": views.new_events(game, self.state),
                **{key: value(game) if callable(value) else value for key, value in extras.items()}}


@contextmanager
def turn(session: Session, game_id: int, week: int | None = None, overall: int | None = None) -> Iterator[Turn]:
    """The game loaded under its lock. `week` is the week the caller believes it is, `overall` the draft
    pick it believes is on the clock: 409 when either is given and wrong."""
    with errors(), game_lock(game_id):
        row, settings, state = rs.load(session, game_id)
        if week is not None and week != state.week:
            raise HTTPException(409, f"This league is in week {state.week}, not week {week}. Reload it.")
        if overall is not None and overall != len(state.picks) + 1:
            raise HTTPException(409, f"The draft is at pick {len(state.picks) + 1}, not pick {overall}. Reload it.")
        data = _season_data(session, settings, row.id)
        yield Turn(session, row, settings, state, data)


# ---------------- seasons ----------------

@router.get("/seasons")
def seasons(session: Session = Depends(get_session)) -> dict:
    """Which past seasons can be played, the simulated ones (live, future), and the season import that is running."""
    year = replay_live.year(session)                    # read once: every answer below needs it
    return {"results": [replay_pool.readiness(session, season) for season in year.seasons],
            "simulated": sim_setup.simulated(session, year), "prepare": replay_data.status(session)}


@router.post("/seasons/{season}/prepare")
def prepare_season(season: int, force: bool = False, session: Session = Depends(get_session)) -> dict:
    """Import a season's data in the background; poll GET /seasons. One import runs at a time."""
    with errors():
        return replay_data.start_prepare(season, bind=session.get_bind(), force=force)


# ---------------- games ----------------

class RulesBody(BaseModel):
    """A league's custom key settings (replay_scoring.ScoringRules): each bound + step allows exactly its values
    there (TE_PREMIUMS 0/0.5/1, PASS_TDS 4/6, TURNOVERS -2/-1); replay_scoring.problem checks them again."""
    reception: float = Field(..., ge=0, le=replay_scoring.RECEPTION_MAX, multiple_of=replay_scoring.RECEPTION_STEP)
    te_premium: float = Field(0.0, ge=min(replay_scoring.TE_PREMIUMS), le=max(replay_scoring.TE_PREMIUMS),
                              multiple_of=0.5)
    pass_td: float = Field(replay_scoring.BASE_PASS_TD, ge=min(replay_scoring.PASS_TDS),
                           le=max(replay_scoring.PASS_TDS), multiple_of=2)
    turnover: float = Field(replay_scoring.BASE_TURNOVER, ge=min(replay_scoring.TURNOVERS),
                            le=max(replay_scoring.TURNOVERS), multiple_of=1)


class CreateBody(BaseModel):
    season: int | Literal["random"]
    name: str | None = Field(None, max_length=MAX_NAME_LENGTH)
    teams: int = Field(12, ge=min(rs.VALID_TEAMS), le=max(rs.VALID_TEAMS))
    playoff_end_week: Literal[16, 17, 18] = 17            # the championship week
    playoff_teams: int | None = Field(None, ge=rs.PLAYOFF_TEAMS_RANGE[0], le=rs.PLAYOFF_TEAMS_RANGE[1])  # None: default
    scoring: Scoring = "half"
    draft_slot: int | None = Field(None, ge=1, le=max(rs.VALID_TEAMS))     # None: drawn from the seed
    waiver_mode: Literal["wednesday", "faab", "none"] = "wednesday"
    difficulty: Literal["normal", "hard"] = "normal"
    seed: int | None = Field(None, ge=0, le=MAX_SEED)
    starters: dict[str, int] | None = Field(None, max_length=len(replay_slots.SLOTS))   # slot -> count; None: standard
    scoring_rules: RulesBody | None = None              # None: the `scoring` preset as is
    start: Literal["full", "now"] = "full"              # "now": the live season, drafted today, from the next week


def _lineup_and_scoring(body: CreateBody) -> tuple[dict[str, int], str, replay_scoring.ScoringRules | None]:
    """(starters, scoring, rules) from the body; ValueError (422) for a lineup or rules outside the bounds."""
    starters = dict(body.starters) if body.starters is not None else dict(rs.DEFAULT_STARTERS)
    problem = replay_slots.problem(starters)
    if problem:
        raise ValueError(problem)
    rules = replay_scoring.ScoringRules(**body.scoring_rules.model_dump()) if body.scoring_rules else None
    problem = rules and replay_scoring.problem(rules)
    if problem:
        raise ValueError(problem)
    return (starters, *replay_scoring.normalize(body.scoring, rules))


def _season(session: Session, wanted: int | str, seed: int, end_week: int) -> tuple[int, str, int]:
    """(season, kind, last real week) as sim_setup.kind_of; "random" picks a ready complete past season."""
    if wanted != "random":
        return (int(wanted), *sim_setup.kind_of(session, int(wanted)))
    ready = replay_pool.ready_last_weeks(session)
    if not ready:
        raise NoDataError("No season is ready to play yet: prepare one first.")
    fits = [season for season, last in ready.items() if last >= end_week]
    if not fits:
        raise ValueError(f"No prepared season has a week {end_week}: pick an earlier championship week.")
    return int(rs.rng(seed, "season").choice(fits)), sim_setup.REPLAY, 0


def _window(body: CreateBody, kind: str, through: int) -> dict:
    """Settings for when the league plays: a league starting now (the live season only) drafts today and plays
    from week through + 1, by default with a 4-team bracket, the deadline inside its shorter season."""
    now = body.start == "now"
    if now and kind != sim_setup.LIVE:
        raise ValueError("Only the season in progress can start now: other leagues start with a preseason draft.")
    playoff_teams = body.playoff_teams or (sim_setup.NOW_PLAYOFF_TEAMS if now else rs.default_playoff_teams(body.teams))
    window = {"playoff_teams": playoff_teams, "sim_from": None if kind == sim_setup.REPLAY else through + 1,
              "regular_season_weeks": body.playoff_end_week - rs.playoff_rounds(playoff_teams)}
    if now:
        regular, deadline = sim_setup.now_window(through, body.playoff_end_week, playoff_teams)
        window.update(start_week=through + 1, regular_season_weeks=regular, trade_deadline_week=deadline)
    return window


@router.get("/games")
def games(session: Session = Depends(get_session)) -> dict:
    return {"results": [league_views.listing(row) for row in rs.list_leagues(session)]}


@router.post("/games")
def create_game(body: CreateBody, session: Session = Depends(get_session)) -> dict:
    """A new league, drafted up to the user's first pick. Season "random": a seeded choice of a ready one. A live
    or future season is built from the seed and frozen with the league, in the same transaction."""
    seed = body.seed if body.seed is not None else secrets.randbelow(MAX_SEED + 1)
    with errors():
        starters, scoring, rules = _lineup_and_scoring(body)        # 422 before any season lookup
        season, kind, through = _season(session, body.season, seed, body.playoff_end_week)
        slot = body.draft_slot or int(rs.rng(seed, "slot").integers(1, body.teams + 1))
        settings = Settings(season=season, teams=body.teams, scoring=scoring, draft_slot=slot,
                            waiver_mode=body.waiver_mode, difficulty=body.difficulty, starters=starters,
                            scoring_rules=rules, adp_format=replay_adp_format.wanted(scoring, rules, starters),
                            **_window(body, kind, through))
        simulated = settings.sim_from is not None
        blob, data = (_build(session, settings, seed) if simulated else
                      (None, replay_pool.load_season_data(session, season, scoring, rules, settings.adp_format)))
        state = rg.create_game(settings, data, seed)
        name = (body.name or "").strip() or f"{season} {'Sim' if simulated else 'Replay'}"
        row = rs.create(session, name, settings, state, seed, commit=not simulated)
        if simulated:                               # never a league without its season
            replay_blob.store(session, row.id, kind, through, blob)
            session.commit()
            replay_blob.remember(session, row.id, data)
        game = _game(row, settings, state, data)
        return {"game": views.summary(game), "events": views.new_events(game, None)}


def _build(session: Session, settings: Settings, seed: int) -> tuple[bytes, SeasonData]:
    """sim_setup.build. NoDataError (the season isn't ready, or future_problem's reason) stays a 503; anything else
    is a fault in the simulation, never bad input: logged, and a 500 naming only the exception's type."""
    try:
        return sim_setup.build(session, settings, seed)
    except NoDataError:
        raise
    except Exception as exc:
        log.exception("Building the %s season failed", settings.season)
        raise HTTPException(500, f"The season could not be built ({type(exc).__name__}).") from exc


@router.get("/games/{game_id}")
def game_summary(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, views.summary)


@router.post("/games/{game_id}/delete")
def delete_game(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    with errors(), game_lock(game_id):
        rs.delete(session, game_id)
        replay_blob.forget(session, game_id)
    return {"deleted": game_id}


# ---------------- a game's screens ----------------

@router.get("/games/{game_id}/draft")
def draft_board(game_id: GameId, position: str | None = None, search: str | None = Query(None, max_length=60),
                sort: Literal["rank", "adp", "projection"] = "rank",
                direction: Literal["asc", "desc"] | None = None,          # None: asc for rank/adp, desc for projection
                limit: int = Limit, offset: int = Offset, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: views.draft(game, position, search, limit, offset, sort, direction))


@router.get("/games/{game_id}/team")
def team(game_id: GameId, team_id: int | None = TeamId, week: int | None = Week,
         session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: views.team(game, team_id, week))


@router.get("/games/{game_id}/matchup")
def matchup(game_id: GameId, week: int | None = Week, team_id: int | None = TeamId,
            session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: views.matchup(game, week, team_id))


@router.get("/games/{game_id}/scoreboard")
def scoreboard(game_id: GameId, week: int | None = Week, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: views.scoreboard(game, week))


@router.get("/games/{game_id}/standings")
def standings(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, league_views.standings)


@router.get("/games/{game_id}/playoffs")
def playoffs(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, league_views.playoffs)


@router.get("/games/{game_id}/schedule")
def schedule(game_id: GameId, team_id: int | None = TeamId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: league_views.schedule(game, team_id))


@router.get("/games/{game_id}/activity")
def activity(game_id: GameId, kind: str | None = Query(None, max_length=80), limit: int = Limit,
             offset: int = Offset, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: league_views.activity(game, kind, limit, offset))


@router.get("/games/{game_id}/players")
def players(game_id: GameId, position: str | None = None, search: str | None = Query(None, max_length=60),
            status: str | None = None, sort: str = "value", limit: int = Limit, offset: int = Offset,
            direction: str | None = Query(None, alias="dir"), stats: str | None = None,
            stat_week: int | None = Query(None, ge=1, le=22),
            session: Session = Depends(get_session)) -> dict:
    """The waiver wire: the whole pool, filtered, sorted and paged, with the chosen stat columns."""
    return show(session, game_id, lambda game: league_views.players(game, position, search, status, sort, limit,
                                                                    offset, direction, stats, stat_week))


@router.get("/games/{game_id}/players/{player_id}")
def player(game_id: GameId, player_id: int, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, lambda game: league_views.player(game, player_id))


@router.get("/games/{game_id}/claims")
def claims(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, league_views.claims)


@router.get("/games/{game_id}/trades")
def trades(game_id: GameId, session: Session = Depends(get_session)) -> dict:
    return show(session, game_id, league_views.trades)
