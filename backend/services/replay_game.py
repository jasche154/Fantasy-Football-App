"""Replay League: the orchestrator. It owns the order things happen in; the engine modules never call
each other.

    create_game -> pick / auto_pick / auto_draft -> (draft done) start_week
    week, "wednesday" waivers:  advance("wednesday") -> advance("play") -> ...
    week, no waivers:           advance("play") -> ...

- `start_week`: CPU IR moves, CPU free agency, then CPU trades and a possible offer to the user, then every
  dropped player comes off waivers and the phase is "open".
- `advance("wednesday")` (phase "waivers"): CPU IR moves, then CPU claims plus the user's, awarded by rolling
  priority, then `start_week`.
- `advance("play")` (phase "open"): the week is scored. With waivers the phase becomes "waivers" and the CPUs
  make their IR moves on the new week's tags (so none is blocked while the user acts); without them the
  next week starts at once (CPUs move first, then the user can take anyone left).

No hindsight, by construction: every step here hands the engine `data.as_of(state.week)` or a Knowledge
built from it. The only call that receives the full season is `replay_season.play_week`, which reads it
for the points of the week being played and nothing else.

Routes import this module, replay_views and replay_state only: the user's actions below are thin
pass-throughs that add the Knowledge the engine needs.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace

from services import (
    replay_draft, replay_ir, replay_season, replay_state as rs, replay_trade_ai, replay_trades, replay_values as rv,
    replay_waiver_ai, replay_waivers,
)
from services.replay_pool import SeasonData
from services.replay_state import Settings, State
from services.replay_trades import OfferExpired, Verdict       # re-exported for the routes

__all__ = [
    "OfferExpired", "STEPS", "Verdict", "add_drop", "advance", "auto_draft", "auto_pick", "cancel_claim",
    "clear_lineup", "create_game", "drop", "evaluate_trade", "ir_activate", "ir_place", "knowledge", "pick",
    "propose_trade", "reorder_claims", "respond_offer", "set_lineup", "start_week", "submit_claim",
]

WEDNESDAY, PLAY = "wednesday", "play"
STEPS = (WEDNESDAY, PLAY)
SEASON_PHASES = ("waivers", "open")


def knowledge(state: State, settings: Settings, data: SeasonData) -> rv.Knowledge:
    """What everyone knows before `state.week` is played (cached). Views and every engine call use this."""
    return rv.knowledge(data.as_of(state.week), rv.league_settings(settings), state.week, settings.end_week)


# ---------------- creating a game and drafting ----------------

def _check_fits(settings: Settings, data: SeasonData) -> None:
    """ValueError unless this league can be played on that season."""
    if (data.season, data.scoring) != (settings.season, settings.scoring):
        raise ValueError(f"This league plays the {settings.season} season with {settings.scoring} scoring, "
                         f"but the data loaded is {data.season} with {data.scoring} scoring.")
    if settings.end_week > data.last_week:
        raise ValueError(f"The {data.season} season has {data.last_week} weeks: a regular season of "
                         f"{settings.regular_season_weeks} weeks plus {rs.PLAYOFF_WEEKS} playoff weeks is too long.")
    if settings.playoff_teams != replay_season.BRACKET_SIZE:
        raise ValueError(f"Replay leagues play a {replay_season.BRACKET_SIZE}-team playoff bracket.")


def _draft_only(state: State) -> None:
    if state.phase != "draft":
        raise ValueError("The draft is over.")


def _cpus_pick(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """CPUs pick up to the user's next turn; when that completes the draft, week 1 starts."""
    state = replay_draft.run_cpu_picks(state, settings, data.as_of(0), seed)
    return state if state.phase == "draft" else start_week(state, settings, data, seed)


def create_game(settings: Settings, data: SeasonData, seed: int) -> State:
    """A new league in its draft, with the CPUs' picks made up to the user's first turn.

    ValueError when the league does not fit the season (too many weeks, another season or scoring)."""
    _check_fits(settings, data)
    return _cpus_pick(replay_draft.new_game(settings, seed), settings, data, seed)


def pick(state: State, settings: Settings, data: SeasonData, seed: int, player_id: int) -> State:
    """The user drafts that player, then the CPUs pick up to the user's next turn."""
    _draft_only(state)
    return _cpus_pick(replay_draft.make_pick(state, settings, data.as_of(0), player_id), settings, data, seed)


def auto_pick(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The user's pick made for them with CPU logic (no noise), then the CPUs' picks."""
    _draft_only(state)
    return _cpus_pick(replay_draft.autopick_user(state, settings, data.as_of(0), seed), settings, data, seed)


def auto_draft(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """Finish the whole draft automatically: every remaining user pick is an auto pick."""
    _draft_only(state)
    while state.phase == "draft":
        state = auto_pick(state, settings, data, seed)
    return state


# ---------------- the week ----------------

def start_week(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The CPUs' start-of-week moves: IR, free agency, then trades (and maybe an offer to the user). Then
    every dropped player comes off waivers and the phase is "open": the user can add anyone left."""
    if state.phase not in SEASON_PHASES:
        raise ValueError("A week can only start during the season.")
    know = knowledge(state, settings, data)
    state = replay_waiver_ai.cpu_ir(state, settings, know, seed)
    state = replay_waiver_ai.cpu_free_agency(state, settings, know, seed)
    state = replay_trade_ai.cpu_trades(state, settings, know, seed)
    return replace(state, locked=(), phase="open")


def _wednesday(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    if state.phase != "waivers":
        reason = ("This league has no weekly waiver run" if settings.waiver_mode == "none"
                  else f"Week {state.week}'s waivers have already run")
        raise ValueError(f"{reason}: play week {state.week} instead.")
    know = knowledge(state, settings, data)
    state = replay_waiver_ai.cpu_ir(state, settings, know, seed)
    claims = replay_waiver_ai.cpu_claims(state, settings, know, seed)
    return start_week(replay_waivers.process_claims(state, settings, know, claims), settings, data, seed)


def _play(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    if state.phase != "open":
        raise ValueError(f"Run week {state.week}'s waivers before playing it.")
    played = replay_season.play_week(state, settings, data, knowledge(state, settings, data), seed)
    if played.phase == "waivers":       # CPUs' IR moves as the waiver phase opens: none is blocked while the user acts
        return replay_waiver_ai.cpu_ir(played, settings, knowledge(played, settings, data), seed)
    # Without waivers the next week starts right away: CPUs move first, then the user can grab anyone.
    return start_week(played, settings, data, seed) if played.phase == "open" else played


def advance(state: State, settings: Settings, data: SeasonData, seed: int, step: str) -> State:
    """Move time forward one step: "wednesday" runs the waivers (phase "waivers" only), "play" scores the
    week (phase "open" only). `data` is the full season; only the scoring of the week reads past today."""
    if step not in STEPS:
        raise ValueError(f"Unknown step '{step}'. Valid: {list(STEPS)}")
    if state.phase == "draft":
        raise ValueError("Finish the draft before starting the season.")
    if state.phase == "complete":
        raise ValueError("This season is over.")
    return (_wednesday if step == WEDNESDAY else _play)(state, settings, data, seed)


# ---------------- the user's actions (thin pass-throughs) ----------------

def set_lineup(state: State, settings: Settings, data: SeasonData, starters: Mapping[str, Sequence[int]]) -> State:
    return replay_season.set_lineup(state, settings, knowledge(state, settings, data), starters)


def clear_lineup(state: State) -> State:
    return replay_season.clear_lineup(state)


def submit_claim(state: State, settings: Settings, data: SeasonData, add: int, drop: int | None = None) -> State:
    return replay_waivers.submit_claim(state, settings, knowledge(state, settings, data), add, drop)


def cancel_claim(state: State, claim_id: int) -> State:
    return replay_waivers.cancel_claim(state, claim_id)


def reorder_claims(state: State, ids: Sequence[int]) -> State:
    return replay_waivers.reorder_claims(state, ids)


def add_drop(state: State, settings: Settings, data: SeasonData, add: int, drop: int | None = None) -> State:
    """The user adds a free agent (phase "open"), dropping `drop` when the roster is full."""
    know = knowledge(state, settings, data)
    return replay_waivers.add_drop(state, settings, know, rs.user_team(state).id, add, drop)


def drop(state: State, settings: Settings, data: SeasonData, player_id: int) -> State:
    know = knowledge(state, settings, data)
    return replay_waivers.drop(state, settings, know, rs.user_team(state).id, player_id)


def ir_place(state: State, settings: Settings, data: SeasonData, player_id: int) -> State:
    """The user moves an eligible player from the active roster to IR."""
    know = knowledge(state, settings, data)
    return replay_ir.place(state, know, rs.user_team(state).id, player_id)


def ir_activate(state: State, settings: Settings, data: SeasonData, player_id: int, drop: int | None = None,
                swap: int | None = None) -> State:
    """The user brings a player back from IR (dropping `drop`, or moving `swap` to IR in his place)."""
    know = knowledge(state, settings, data)
    return replay_ir.activate(state, settings, know, rs.user_team(state).id, player_id, drop, swap)


def evaluate_trade(state: State, settings: Settings, data: SeasonData, to_team: int, give: Sequence[int],
                   get: Sequence[int], user_drops: Sequence[int] = ()) -> Verdict:
    """Would that CPU take the user's `give` for its `get`? Changes nothing."""
    know = knowledge(state, settings, data)
    return replay_trades.evaluate(state, settings, know, rs.user_team(state).id, to_team, give, get, user_drops)


def propose_trade(state: State, settings: Settings, data: SeasonData, to_team: int, give: Sequence[int],
                  get: Sequence[int], user_drops: Sequence[int] = ()) -> tuple[State, Verdict]:
    return replay_trades.propose(state, settings, knowledge(state, settings, data), to_team, give, get, user_drops)


def respond_offer(state: State, settings: Settings, data: SeasonData, offer_id: int, accept: bool,
                  user_drops: Sequence[int] = ()) -> State:
    """The user answers a CPU's offer. OfferExpired (a ValueError) carries the state to save when the
    offer no longer stands."""
    know = knowledge(state, settings, data)
    return replay_trades.respond(state, settings, know, offer_id, accept, user_drops)
