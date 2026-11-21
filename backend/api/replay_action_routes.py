"""Replay League: what the user does in a game (draft, lineup, waivers, trades, moving time forward).

Every route here answers {game: Summary, events: Event[], ...extras} (see services/replay_views.py) and
runs under the replay lock (`replay_routes.turn`). A declined trade is a 200 with its verdict.
"""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from api.replay_routes import GameId, errors, open_game, turn
from database.db import get_session
from services import replay_game as rg, replay_views as views, replay_views_league as league_views

router = APIRouter(prefix="/api/replay/games/{game_id}")

MAX_LISTED = 20                 # ids in one request body; the engine applies the real limits with a clear message
MAX_WEEK = 22
PlayerId = Field(..., ge=1)
Ids = Field(default_factory=list, max_length=MAX_LISTED)


class PickBody(BaseModel):
    player_id: int = PlayerId
    overall: int | None = Field(None, ge=1)         # draft: the pick the page shows on the clock, 409 when stale


class LineupBody(BaseModel):
    # null: back to the best lineup. Each slot's list is bounded too: it is checked while the game's lock is held.
    starters: dict[str, Annotated[list[int], Field(max_length=MAX_LISTED)]] | None = Field(..., max_length=MAX_LISTED)


class ClaimBody(BaseModel):
    add: int = PlayerId
    drop: int | None = Field(None, ge=1)


class ActivateBody(BaseModel):
    player_id: int = PlayerId
    drop: int | None = Field(None, ge=1)
    swap: int | None = Field(None, ge=1)            # an injured active player who takes the IR slot instead


class ReorderBody(BaseModel):
    ids: list[int] = Field(..., max_length=100)


class AdvanceBody(BaseModel):
    step: Literal["wednesday", "play"]
    week: int = Field(..., ge=1, le=MAX_WEEK)       # the week the page is showing: 409 when the league moved on


class TradeBody(BaseModel):
    to_team: int = Field(..., ge=1)
    give: list[int] = Field(..., max_length=MAX_LISTED)
    get: list[int] = Field(..., max_length=MAX_LISTED)
    drops: list[int] = Ids                          # the user's own drops when the deal overfills the roster


class RespondBody(BaseModel):
    accept: bool
    drops: list[int] = Ids


@router.post("/draft/pick")
def draft_pick(game_id: GameId, body: PickBody, session: Session = Depends(get_session)) -> dict:
    """The user's pick; the CPUs then pick up to the user's next turn (`events` lists them)."""
    with turn(session, game_id, overall=body.overall) as t:
        return t.done(rg.pick(*t.args, t.seed, body.player_id))


@router.post("/draft/auto")
def draft_auto(game_id: GameId, whole_draft: bool = False, overall: int | None = Query(None, ge=1),
               session: Session = Depends(get_session)) -> dict:
    """Auto-pick for the user: this pick, or with whole_draft=true every pick left. `overall`: see PickBody."""
    with turn(session, game_id, overall=overall) as t:
        return t.done((rg.auto_draft if whole_draft else rg.auto_pick)(*t.args, t.seed))


@router.post("/lineup")
def set_lineup(game_id: GameId, body: LineupBody, session: Session = Depends(get_session)) -> dict:
    with turn(session, game_id) as t:
        state = rg.clear_lineup(t.state) if body.starters is None else rg.set_lineup(*t.args, body.starters)
        return t.done(state, team=views.team)


@router.post("/claims")
def submit_claim(game_id: GameId, body: ClaimBody, session: Session = Depends(get_session)) -> dict:
    with turn(session, game_id) as t:
        return t.done(rg.submit_claim(*t.args, body.add, body.drop))


@router.post("/claims/reorder")
def reorder_claims(game_id: GameId, body: ReorderBody, session: Session = Depends(get_session)) -> dict:
    with turn(session, game_id) as t:
        return t.done(rg.reorder_claims(t.state, body.ids))


@router.post("/claims/{claim_id}/cancel")
def cancel_claim(game_id: GameId, claim_id: int, session: Session = Depends(get_session)) -> dict:
    with turn(session, game_id) as t:
        return t.done(rg.cancel_claim(t.state, claim_id))


@router.post("/add-drop")
def add_drop(game_id: GameId, body: ClaimBody, session: Session = Depends(get_session)) -> dict:
    """Add a free agent now (after the week's waiver run), dropping `drop` when the roster is full."""
    with turn(session, game_id) as t:
        return t.done(rg.add_drop(*t.args, body.add, body.drop))


@router.post("/drop")
def drop(game_id: GameId, body: PickBody, session: Session = Depends(get_session)) -> dict:
    with turn(session, game_id) as t:
        return t.done(rg.drop(*t.args, body.player_id))


@router.post("/ir/place")
def ir_place(game_id: GameId, body: PickBody, session: Session = Depends(get_session)) -> dict:
    """Move a player tagged O, IR or PUP this week to the team's IR slot."""
    with turn(session, game_id) as t:
        return t.done(rg.ir_place(*t.args, body.player_id))


@router.post("/ir/activate")
def ir_activate(game_id: GameId, body: ActivateBody, session: Session = Depends(get_session)) -> dict:
    """Bring a player back from IR, dropping `drop` when the roster is full or moving `swap` to IR instead."""
    with turn(session, game_id) as t:
        return t.done(rg.ir_activate(*t.args, body.player_id, body.drop, body.swap))


@router.post("/advance")
def advance(game_id: GameId, body: AdvanceBody, session: Session = Depends(get_session)) -> dict:
    """Move time forward: "wednesday" runs the waivers, "play" scores the week."""
    with turn(session, game_id, week=body.week) as t:
        return t.done(rg.advance(*t.args, t.seed, body.step), step=body.step, week=body.week)


@router.post("/trades/evaluate")
def evaluate_trade(game_id: GameId, body: TradeBody, session: Session = Depends(get_session)) -> dict:
    """Would that CPU take it? Changes nothing."""
    with errors():
        game = open_game(session, game_id)
        found = rg.evaluate_trade(game.state, game.settings, game.full_season, body.to_team, body.give,
                                  body.get, body.drops)
        return {"verdict": league_views.verdict(game, found)}


@router.post("/trades/propose")
def propose_trade(game_id: GameId, body: TradeBody, session: Session = Depends(get_session)) -> dict:
    """Offer a CPU a trade: carried out at once when it accepts, otherwise the verdict says why not."""
    with turn(session, game_id) as t:
        state, found = rg.propose_trade(*t.args, body.to_team, body.give, body.get, body.drops)
        return t.done(state, verdict=lambda game: league_views.verdict(game, found), executed=found.accepted)


@router.post("/trades/offers/{offer_id}/respond")
def respond_offer(game_id: GameId, offer_id: int, body: RespondBody, session: Session = Depends(get_session)) -> dict:
    """Accept or decline a CPU's offer. One that no longer stands is saved as expired and answered with
    expired=true and the reason."""
    with turn(session, game_id) as t:
        try:
            state = rg.respond_offer(*t.args, offer_id, body.accept, body.drops)
        except rg.OfferExpired as exc:
            found = exc.verdict
            return t.done(exc.state, accepted=False, expired=True, message=str(exc),
                          verdict=(lambda game: league_views.verdict(game, found)) if found else None)
        return t.done(state, accepted=body.accept, expired=False, message=None, verdict=None)
