"""Replay League: the orchestrator. It owns the order things happen in; the engine modules never call
each other.

    create_game -> pick / auto_pick / auto_draft -> (draft done) start_week
    week, "wednesday"/"faab":   advance("wednesday") -> advance("play") -> ...
    week, no waivers:           advance("play") -> ...

- Chat: a third CPU may react to any trade (`replay_chat.trade_reactions`), every CPU offer to the user
  carries its reason (`replay_chat.label_offers`) and a CPU that turns the user's proposal down says why
  (`replay_chat.decline_reply`, stored on the declined Offer as `reply`). A haggler always counters when the
  user declines his opening offer (`replay_counter.haggle_counter`).
- `start_week`: CPU IR moves, CPU free agency, then worried CPUs shop a player to the user
  (`replay_mood.refresh_shops`, with a message each: after the roster moves, so no shop is opened on a player
  his manager then drops or stashes), then CPU trades and a possible offer to the user, then every
  dropped player comes off waivers, the phase is "open" and each CPU covers any starting slot a trade left
  it unable to fill (`replay_cover.cpu_cover`). A trade accepted in the open phase is covered at once; one
  in the waivers phase waits for this, as every unrostered player is on waivers until then.
- Trades are open through `trade_deadline_week` and its waiver phase (`replay_state.trades_open`): the
  deadline passes when the user presses "Sim to Wednesday" after the deadline week's games. A declined
  proposal may come back as the CPU's counter offer; the user may counter a CPU's offer too.
- `advance("wednesday")` (phase "waivers"): CPU IR moves, then CPU claims plus the user's, awarded by rolling
  priority, then `start_week`.
- `advance("play")` (phase "open"): the week is scored and the CPUs talk about it (`replay_chat.after_week`).
  With waivers the phase becomes "waivers" and the CPUs
  make their IR moves on the new week's tags (so none is blocked while the user acts); without them the
  next week starts at once (CPUs move first, then the user can take anyone left).

No hindsight, by construction: every step here hands the engine `data.as_of(state.week)` or a Knowledge
built from it. The only call that receives the full season is `replay_season.play_week`, which reads it
for the points of the week being played and nothing else.

A "jump in now" league (settings.start_week K > 1) drafts in week K on `data.as_of(K)` (State.week is K
during its draft, replay_state.draft_week) and plays weeks K..end_week; nothing else here changes.

Routes import this module, replay_views and replay_state only: the user's actions below are thin
pass-throughs that add the Knowledge the engine needs.
"""
from collections.abc import Mapping, Sequence
from dataclasses import replace

from services import (
    replay_chat, replay_counter, replay_draft, replay_ir, replay_mood, replay_season, replay_state as rs,
    replay_trade_ai, replay_trades, replay_values as rv, replay_waiver_ai, replay_waivers,
)
from services.replay_pool import SeasonData
from services.replay_state import Offer, Settings, State
from services.replay_trades import OfferExpired, Verdict       # re-exported for the routes

__all__ = [
    "OfferExpired", "STEPS", "Verdict", "add_drop", "advance", "auto_draft", "auto_pick", "cancel_claim",
    "clear_lineup", "counter_offer", "create_game", "drop", "evaluate_trade", "ir_activate", "ir_place", "knowledge", "pick",
    "propose_trade", "reorder_claims", "respond_offer", "set_claim_bid", "set_lineup", "start_week",
    "submit_claim",
]

WEDNESDAY, PLAY = "wednesday", "play"
Proposed = tuple[State, Verdict, Offer | None, str | None]       # (state, verdict, counter, the CPU's reply)
STEPS = (WEDNESDAY, PLAY)
SEASON_PHASES = ("waivers", "open")


def knowledge(state: State, settings: Settings, data: SeasonData, *, seed: int) -> rv.Knowledge:
    """What everyone knows before `state.week` is played (cached). Views and every engine call use this;
    `seed` is the game's, for its injury-news noise (views pass the same one)."""
    return rv.knowledge(data.as_of(state.week), rv.league_settings(settings), state.week, settings.end_week, seed,
                        drafted=rs.draft_week(settings))


# ---------------- creating a game and drafting ----------------

def _check_fits(settings: Settings, data: SeasonData) -> None:
    """ValueError unless this league can be played on that season."""
    if (data.season, data.scoring, data.rules) != (settings.season, settings.scoring, settings.scoring_rules):
        raise ValueError(f"This league plays the {settings.season} season with {settings.scoring} scoring, "
                         f"but the data loaded is {data.season} with {data.scoring} scoring.")
    if settings.end_week > data.last_week:
        raise ValueError(f"The {data.season} season has {data.last_week} weeks: a regular season of "
                         f"{settings.regular_season_weeks} weeks with playoffs in weeks "
                         f"{settings.regular_season_weeks + 1}-{settings.end_week} is too long.")


def _draft_only(state: State) -> None:
    if state.phase != "draft":
        raise ValueError("The draft is over.")


def _cpus_pick(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """CPUs pick up to the user's next turn; when that completes the draft, the first week (start_week) starts."""
    state = replay_draft.run_cpu_picks(state, settings, data.as_of(state.week), seed)
    return state if state.phase == "draft" else start_week(state, settings, data, seed)


def create_game(settings: Settings, data: SeasonData, seed: int) -> State:
    """A new league in its draft, with the CPUs' picks made up to the user's first turn.

    ValueError when the league does not fit the season (too many weeks, another season or scoring)."""
    _check_fits(settings, data)
    return _cpus_pick(replay_draft.new_game(settings, seed), settings, data, seed)


def pick(state: State, settings: Settings, data: SeasonData, seed: int, player_id: int) -> State:
    """The user drafts that player, then the CPUs pick up to the user's next turn."""
    _draft_only(state)
    return _cpus_pick(replay_draft.make_pick(state, settings, data.as_of(state.week), player_id), settings, data, seed)


def auto_pick(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The user's pick made for them with CPU logic (no noise), then the CPUs' picks."""
    _draft_only(state)
    return _cpus_pick(replay_draft.autopick_user(state, settings, data.as_of(state.week), seed), settings, data, seed)


def auto_draft(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """Finish the whole draft automatically: every remaining user pick is an auto pick."""
    _draft_only(state)
    while state.phase == "draft":
        state = auto_pick(state, settings, data, seed)
    return state


# ---------------- the week ----------------

def start_week(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The CPUs' start-of-week moves: IR, free agency, this week's shops (with the shopping managers'
    messages), then trades (and maybe an offer to the user). Then
    every dropped player comes off waivers and the phase is "open": CPUs cover the starting slots their trades
    emptied (this week's, and any made in the waiver phase), then the user can add anyone left."""
    if state.phase not in SEASON_PHASES:
        raise ValueError("A week can only start during the season.")
    know = knowledge(state, settings, data, seed=seed)
    state = replay_waiver_ai.cpu_ir(state, settings, know, seed)
    state = replay_waiver_ai.cpu_free_agency(state, settings, know, seed)
    played = data.as_of(state.week)                 # last week's games are known now (snap counts, tags)
    state, opened = replay_mood.refresh_shops(state, settings, know, seed, played)
    state = replay_chat.shop_lines(state, opened, know, seed)
    before = len(state.offers)
    state = replay_trade_ai.cpu_trades(state, settings, know, seed)
    made = [offer.id for offer in state.offers[before:]]
    state = replay_chat.label_offers(replay_chat.trade_reactions(state, made, know, seed), know, seed, played, settings)
    state = replace(state, locked=(), phase="open")
    return replay_waiver_ai.cpu_cover(state, settings, know, [team.id for team in state.teams])


def _wednesday(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    if state.phase != "waivers":
        reason = ("This league has no weekly waiver run" if settings.waiver_mode == "none"
                  else f"Week {state.week}'s waivers have already run")
        raise ValueError(f"{reason}: play week {state.week} instead.")
    know = knowledge(state, settings, data, seed=seed)
    state = replay_waiver_ai.cpu_ir(state, settings, know, seed)
    claims = replay_waiver_ai.cpu_claims(state, settings, know, seed)
    return start_week(replay_waivers.process_claims(state, settings, know, claims), settings, data, seed)


def _after_games(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """The chat about the week just scored (this week's shops open at start_week, after the CPUs' moves)."""
    know = knowledge(state, settings, data, seed=seed)
    played = data.as_of(state.results[-1].week + 1)     # the games just scored are known now (snap counts, tags)
    return replay_chat.after_week(state, settings, know, seed, played)


def _play(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    if state.phase != "open":
        raise ValueError(f"Run week {state.week}'s waivers before playing it.")
    played = replay_season.play_week(state, settings, data, knowledge(state, settings, data, seed=seed), seed)
    played = _after_games(played, settings, data, seed)
    if played.phase == "waivers":       # CPUs' IR moves as the waiver phase opens: none is blocked while the user acts
        return replay_waiver_ai.cpu_ir(played, settings, knowledge(played, settings, data, seed=seed), seed)
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
# `seed` is the game's: it only shapes the injury news in the Knowledge, so views and actions agree.

def set_lineup(state: State, settings: Settings, data: SeasonData, starters: Mapping[str, Sequence[int]], *,
               seed: int) -> State:
    return replay_season.set_lineup(state, settings, knowledge(state, settings, data, seed=seed), starters)


def clear_lineup(state: State) -> State:
    return replay_season.clear_lineup(state)


def submit_claim(state: State, settings: Settings, data: SeasonData, add: int, drop: int | None = None,
                 bid: int | None = None, *, seed: int) -> State:
    return replay_waivers.submit_claim(state, settings, knowledge(state, settings, data, seed=seed), add, drop, bid)


def set_claim_bid(state: State, settings: Settings, claim_id: int, bid: int) -> State:
    return replay_waivers.set_claim_bid(state, settings, claim_id, bid)


def cancel_claim(state: State, claim_id: int) -> State:
    return replay_waivers.cancel_claim(state, claim_id)


def reorder_claims(state: State, ids: Sequence[int]) -> State:
    return replay_waivers.reorder_claims(state, ids)


def add_drop(state: State, settings: Settings, data: SeasonData, add: int, drop: int | None = None, *,
             seed: int) -> State:
    """The user adds a free agent (phase "open"), dropping `drop` when the roster is full."""
    know = knowledge(state, settings, data, seed=seed)
    return replay_waivers.add_drop(state, settings, know, rs.user_team(state).id, add, drop)


def drop(state: State, settings: Settings, data: SeasonData, player_id: int, *, seed: int) -> State:
    know = knowledge(state, settings, data, seed=seed)
    return replay_waivers.drop(state, settings, know, rs.user_team(state).id, player_id)


def ir_place(state: State, settings: Settings, data: SeasonData, player_id: int, *, seed: int) -> State:
    """The user moves an eligible player from the active roster to IR."""
    know = knowledge(state, settings, data, seed=seed)
    return replay_ir.place(state, know, rs.user_team(state).id, player_id)


def ir_activate(state: State, settings: Settings, data: SeasonData, player_id: int, drop: int | None = None,
                swap: int | None = None, *, seed: int) -> State:
    """The user brings a player back from IR (dropping `drop`, or moving `swap` to IR in his place)."""
    know = knowledge(state, settings, data, seed=seed)
    return replay_ir.activate(state, settings, know, rs.user_team(state).id, player_id, drop, swap)


# ---------------- trades ----------------

def evaluate_trade(state: State, settings: Settings, data: SeasonData, to_team: int, give: Sequence[int],
                   get: Sequence[int], user_drops: Sequence[int] = (), *, seed: int) -> Verdict:
    """Would that CPU take the user's `give` for its `get`? Changes nothing."""
    know = knowledge(state, settings, data, seed=seed)
    return replay_trades.evaluate(state, settings, know, rs.user_team(state).id, to_team, give, get, user_drops)


def _propose(state: State, settings: Settings, data: SeasonData, seed: int, to_team: int, give: Sequence[int],
             get: Sequence[int], user_drops: Sequence[int], counter_to: int | None) -> Proposed:
    know = knowledge(state, settings, data, seed=seed)
    before = len(state.offers)
    state, verdict = replay_trades.propose(state, settings, know, to_team, give, get, user_drops,
                                           counter_to=counter_to)
    if verdict.accepted:            # the CPU fills any starting slot the deal emptied (waivers phase: at start_week)
        state = replay_chat.trade_reactions(state, [o.id for o in state.offers[before:]], know, seed)
        return replay_waiver_ai.cpu_cover(state, settings, know, [to_team]), verdict, None, None
    if len(state.offers) == before:                 # the same proposal was already declined this week: same reply
        same = next((o for o in reversed(state.offers) if o.status == "declined" and o.to_team == to_team
                     and (o.give, o.get) == (tuple(give), tuple(get)) and o.week == state.week), None)
        return state, verdict, None, same.reply if same else None
    state = replay_chat.decline_reply(state, know, seed, state.offers[-1].id, verdict)
    declined = state.offers[-1]
    countered = replay_counter.counter_offer(state, settings, know, declined, seed, user_drops)
    countered = replay_chat.label_offers(countered, know, seed, data.as_of(state.week), settings)
    answer = next((o for o in countered.offers[len(state.offers):] if o.counter_to == declined.id), None)
    return countered, verdict, answer, declined.reply


def propose_trade(state: State, settings: Settings, data: SeasonData, seed: int, to_team: int, give: Sequence[int],
                  get: Sequence[int], user_drops: Sequence[int] = ()) -> Proposed:
    """The user offers a CPU a trade: (state, verdict, counter, reply). A declined deal may get the CPU's counter
    offer (pending, `counter_to` the user's declined offer; None otherwise) and its one-line reply (None when
    only a league rule or the user's own side stopped it)."""
    return _propose(state, settings, data, seed, to_team, give, get, user_drops, None)


def counter_offer(state: State, settings: Settings, data: SeasonData, seed: int, offer_id: int, give: Sequence[int],
                  get: Sequence[int], user_drops: Sequence[int] = ()) -> Proposed:
    """The user answers a CPU's pending offer with a deal of their own (`give`: what the user sends): the
    CPU's offer is marked "countered" (by `replay_trades.propose`) and the user's goes to the same CPU like
    `propose_trade`."""
    offer = next((o for o in replay_trades.offers_for_user(state) if o.id == offer_id), None)
    if offer is None:
        raise ValueError("There is no pending offer with that id for your team.")
    return _propose(state, settings, data, seed, offer.from_team, give, get, user_drops, offer_id)


def respond_offer(state: State, settings: Settings, data: SeasonData, offer_id: int, accept: bool,
                  user_drops: Sequence[int] = (), *, seed: int) -> State:
    """The user answers a CPU's offer. OfferExpired (a ValueError) carries the state to save when the
    offer no longer stands. Declining a haggler's opening offer brings his counter (a new pending Offer with
    `counter_to` this one, labelled)."""
    know = knowledge(state, settings, data, seed=seed)
    state = replay_trades.respond(state, settings, know, offer_id, accept, user_drops)
    offer = next(o for o in state.offers if o.id == offer_id)
    if not accept:
        haggled = replay_counter.haggle_counter(state, settings, know, offer)
        return state if haggled is state else replay_chat.label_offers(haggled, know, seed, data.as_of(state.week),
                                                                         settings)
    state = replay_chat.trade_reactions(state, [offer_id], know, seed)
    return replay_waiver_ai.cpu_cover(state, settings, know, [offer.from_team])
