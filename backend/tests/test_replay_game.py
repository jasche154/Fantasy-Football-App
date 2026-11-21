"""Replay League: the orchestrator (services/replay_game.py) and the whole game played end to end.

`simulate` plays a league on the fake season from `create_game` to the champion and checks every invariant
after every step (`problems`). Two "users": the idle one auto-drafts and then only presses the button; the
active one (`bot_*`) makes random legal picks, claims, drops, free-agent adds, trade proposals, answers
to offers and lineups from its own seeded dice. Runs are cached, so each league is simulated once.
"""
import time
from dataclasses import dataclass
from functools import lru_cache
from statistics import mean, median

import numpy as np
import pytest

from services import (
    replay_draft, replay_game as rg, replay_ir, replay_season, replay_state as rs, replay_trades,
    replay_values as rv, replay_waivers,
)
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
POSITION = {pid: player.position for pid, player in DATA.players.items()}
DRAFTED = sorted((p for p in DATA.players.values() if p.adp is not None and p.position in ("RB", "WR", "TE")),
                 key=lambda p: (p.adp, p.player_id))
# The active user's leagues: one drafted skill player in six is Out for three weeks somewhere in weeks 3-13,
# so the random user (and every CPU) has players to place on IR, activate, swap and be blocked by.
ACTIVE_DATA = fakes.tagged(DATA, {(p.player_id, week): fakes.Injury("O")
                                  for n, p in enumerate(DRAFTED[:180:6]) for week in range(3 + n % 9, 6 + n % 9)})
BOT_BOARD, BOT_WIRE, BOT_PROPOSALS = 12, 60, 3      # how far down the lists the active user reaches
STEP_BUDGET_SECONDS = 1.0


def league(teams: int = 12, waivers: str = "wednesday", difficulty: str = "normal", slot: int = 1) -> rs.Settings:
    return fakes.fake_settings(teams=teams, waiver_mode=waivers, difficulty=difficulty, draft_slot=slot)


LEAGUES = (league(12, "wednesday", "normal", 4), league(12, "none", "hard", 11), league(12, "none", "normal", 7),
           league(12, "wednesday", "hard", 1), league(8, "wednesday", "normal", 8), league(8, "none", "hard", 2),
           league(8, "none", "normal", 5), league(8, "wednesday", "hard", 3))
IDLE = tuple((settings, seed, False) for seed, settings in enumerate(LEAGUES, start=11))
ACTIVE = tuple((LEAGUES[index], seed, True) for seed, index in enumerate((0, 1, 6, 7), start=3))


@dataclass(frozen=True)
class Step:
    label: str                  # "create" | "auto_draft" | "pick" | "user" | "wednesday" | "play"
    week: int                   # state.week when the step began
    state: rs.State             # after the step
    seconds: float


@dataclass(frozen=True)
class Run:
    settings: rs.Settings
    steps: tuple[Step, ...]
    faults: tuple[str, ...]     # every broken invariant, with the step it showed up after

    @property
    def final(self) -> rs.State:
        return self.steps[-1].state

    def index(self, label: str, week: int) -> int:
        return next(i for i, step in enumerate(self.steps) if (step.label, step.week) == (label, week))


# ---------------- invariants ----------------

def problems(state: rs.State, settings: rs.Settings, before: rs.State | None) -> list[str]:
    """Every invariant this state breaks (empty = healthy)."""
    ids = [pid for team in state.teams for pid in rs.held(team)]
    rostered, user = set(ids), rs.user_team(state).id
    event_ids = [event.id for event in state.events]
    stored = event_ids + [offer.id for offer in state.offers] + [claim.id for claim in state.claims]
    pending = [offer for offer in state.offers if offer.status == "pending"]
    in_season = state.phase != "draft"
    checks = {
        "a player is on two rosters": len(ids) == len(rostered),
        "an unknown player is rostered": rostered <= POSITION.keys(),
        "a roster is over the limit": all(len(team.roster) <= settings.roster_size for team in state.teams),
        "IR over its limit or shared with the roster": all(
            len(team.ir) <= replay_ir.IR_SLOTS and not set(team.ir) & set(team.roster) for team in state.teams),
        "a CPU can't field a legal lineup": not in_season or all(
            rv.can_field_lineup([POSITION[pid] for pid in team.roster], settings)
            for team in state.teams if not team.is_user),
        "waiver order is not a permutation of the teams":
            sorted(state.waiver_order) == (sorted(team.id for team in state.teams) if in_season else []),
        "a waiver-locked player is rostered or listed twice":
            len(set(state.locked)) == len(state.locked) and not rostered & set(state.locked),
        "claims outside the waiver phase": state.phase == "waivers" or not state.claims,
        "a claim is not the user's or is for a rostered player":
            all(claim.team_id == user and claim.add not in rostered for claim in state.claims),
        "event ids are not increasing": all(a < b for a, b in zip(event_ids, event_ids[1:])),
        "ids are reused or ahead of next_id":
            len(set(stored)) == len(stored) and max(stored, default=0) < state.next_id,
        "a pending offer is stale or not to the user":
            all(offer.week == state.week and offer.to_team == user and state.phase != "complete" for offer in pending),
        "results do not match the week":
            len(state.results) == (state.week if state.phase == "complete" else max(0, state.week - 1)),
        "champion and phase disagree": (state.phase == "complete") == (state.champion is not None),
        "JSON does not round-trip": rs.state_from_json(rs.to_json(state)) == state,
        "history was rewritten": before is None or (
            state.week >= before.week and state.events[:len(before.events)] == before.events),
    }
    return [name for name, holds in checks.items() if not holds]


# ---------------- the active user ----------------

def _chance(dice: np.random.Generator, share: float) -> bool:
    return bool(dice.random() < share)


def _some(dice: np.random.Generator, items, low: int, high: int) -> list:
    """Between `low` and `high` different items, at random."""
    return [items[int(i)] for i in dice.permutation(len(items))[:int(dice.integers(low, high + 1))]]


def _a_drop(state: rs.State, settings: rs.Settings, dice: np.random.Generator) -> int | None:
    roster = rs.user_team(state).roster
    needed = len(roster) >= settings.roster_size or (roster and _chance(dice, 0.5))
    return _some(dice, roster, 1, 1)[0] if needed else None


def _overflow_drops(state: rs.State, settings: rs.Settings, dice, give, get) -> list[int]:
    roster = rs.user_team(state).roster
    over = len(roster) - len(give) + len(get) - settings.roster_size
    return _some(dice, [pid for pid in roster if pid not in give], over, over) if over > 0 else []


def bot_pick(state: rs.State, settings: rs.Settings, data, seed: int) -> rs.State:
    dice = rs.rng(seed, "bot-pick", len(state.picks))
    board = replay_draft.available(state, data.as_of(0))[:BOT_BOARD]
    if _chance(dice, 0.5):
        try:
            return rg.pick(state, settings, data, seed, _some(dice, board, 1, 1)[0].player_id)
        except ValueError:
            pass                                    # a pick the roster could not recover from
    return rg.auto_pick(state, settings, data, seed)


def _bot_claims(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    wire = replay_waivers.available_players(state, rg.knowledge(state, settings, data))[:BOT_WIRE]
    shared = _a_drop(state, settings, dice)         # one drop behind several claims: only the first can win
    for value in _some(dice, wire, 0, 3):
        drop = shared if _chance(dice, 0.5) else _a_drop(state, settings, dice)
        state = rg.submit_claim(state, settings, data, value.player_id, drop)
    ids = [claim.id for claim in state.claims]
    if len(ids) > 1:
        state = rg.reorder_claims(state, ids[::-1])
    if ids and _chance(dice, 0.25):
        state = rg.cancel_claim(state, ids[0])
    if rs.user_team(state).roster and _chance(dice, 0.15):
        state = rg.drop(state, settings, data, _some(dice, rs.user_team(state).roster, 1, 1)[0])
    return state


def _bot_answers(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    for offer in replay_trades.offers_for_user(state):
        drops = _overflow_drops(state, settings, dice, offer.get, offer.give)
        try:
            state = rg.respond_offer(state, settings, data, offer.id, _chance(dice, 0.7), drops)
        except rg.OfferExpired as expired:
            state = expired.state
    return state


def _bot_add(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    know = rg.knowledge(state, settings, data)
    free = replay_waivers.available_players(state, know, status="free_agent")[:BOT_WIRE]
    if not free or not _chance(dice, 0.7):
        return state
    return rg.add_drop(state, settings, data, _some(dice, free, 1, 1)[0].player_id, _a_drop(state, settings, dice))


def _bot_trades(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    know = rg.knowledge(state, settings, data)
    for _ in range(BOT_PROPOSALS if state.week <= settings.trade_deadline_week else 0):
        mine = rs.user_team(state).roster
        partner = _some(dice, [team for team in state.teams if not team.is_user], 1, 1)[0]
        get = _some(dice, partner.roster, 1, 2)
        give = _some(dice, mine, 1, 2)
        if _chance(dice, 0.5):                       # an offer a CPU might like: a bit more value than asked
            asked = rv.market_value(know, get[0])
            richer = sorted((know.by_id[pid].market_value, pid) for pid in mine)
            give, get = [next((pid for worth, pid in richer if worth >= asked * 1.1), richer[-1][1])], get[:1]
        drops = _overflow_drops(state, settings, dice, give, get)
        state, _ = rg.propose_trade(state, settings, data, partner.id, give, get, drops)
    return state


def _bot_lineup(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    if _chance(dice, 0.2):
        return rg.clear_lineup(state)
    if _chance(dice, 0.5):
        know = rg.knowledge(state, settings, data)
        lineup = dict(replay_season.starters_for(state, settings, know, rs.user_team(state).id))
        slot = _some(dice, list(lineup), 1, 1)[0]
        return rg.set_lineup(state, settings, data, {**lineup, slot: lineup[slot][:-1]})     # one spot empty
    return state


def _bot_ir(state: rs.State, settings: rs.Settings, data, dice) -> rs.State:
    """Places, activates (with a drop or a swap) and drops from IR; always clears a block first, so the
    adds, claims and trades after it are not refused."""
    know, team = rg.knowledge(state, settings, data), rs.user_team(state)
    hurt = [pid for pid in team.roster if replay_ir.eligible(know, pid)]
    if team.ir and (replay_ir.ineligible(state, know, team.id) or _chance(dice, 0.3)):
        if hurt and _chance(dice, 0.5):
            return rg.ir_activate(state, settings, data, team.ir[0], swap=_some(dice, hurt, 1, 1)[0])
        if _chance(dice, 0.2):
            return rg.drop(state, settings, data, team.ir[0])
        return rg.ir_activate(state, settings, data, team.ir[0], _a_drop(state, settings, dice))
    if hurt and not team.ir and _chance(dice, 0.7):
        return rg.ir_place(state, settings, data, _some(dice, hurt, 1, 1)[0])
    return state


def bot_turn(state: rs.State, settings: rs.Settings, data, seed: int) -> rs.State:
    dice = rs.rng(seed, "bot", state.week, state.phase)
    acts = (_bot_claims,) if state.phase == "waivers" else (_bot_answers, _bot_add, _bot_trades)
    for act in (_bot_ir, *acts, _bot_lineup):
        state = act(state, settings, data, dice)
    return state


# ---------------- playing a league ----------------

@lru_cache(maxsize=None)
def changed(after_week: int, active: bool):
    return fakes.future_changed(ACTIVE_DATA if active else DATA, after_week)


def play(settings: rs.Settings, seed: int, active: bool = False, changed_after: int | None = None,
         last_week: int | None = None, checked: bool = True) -> Run:
    """The league from creation to its champion (or through `last_week`), every step timed and checked.
    (`checked=False` is for a replay that is compared state by state with a run that was checked.)"""
    data = (ACTIVE_DATA if active else DATA) if changed_after is None else changed(changed_after, active)
    steps: list[Step] = []
    faults: list[str] = []

    def do(label: str, before: rs.State | None, action) -> rs.State:
        started = time.perf_counter()
        after = action()
        steps.append(Step(label, before.week if before else 0, after, time.perf_counter() - started))
        found = problems(after, settings, before) if checked else ()
        faults.extend(f"after {label}, week {steps[-1].week}: {fault}" for fault in found)
        return after

    state = do("create", None, lambda: rg.create_game(settings, data, seed))
    while state.phase == "draft":
        state = (do("pick", state, lambda: bot_pick(state, settings, data, seed)) if active
                 else do("auto_draft", state, lambda: rg.auto_draft(state, settings, data, seed)))
    while state.phase != "complete" and (last_week is None or state.week <= last_week):
        if active:
            state = do("user", state, lambda: bot_turn(state, settings, data, seed))
        step = rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY
        state = do(step, state, lambda: rg.advance(state, settings, data, seed, step))
    return Run(settings, tuple(steps), tuple(faults))


simulate = lru_cache(maxsize=None)(play)


def user_place(run: Run) -> int:
    table, user = replay_season.final_standings(run.final, run.settings), rs.user_team(run.final).id
    return next(row["place"] for row in table if row["team_id"] == user)


def cpu_trades(run: Run) -> int:
    """Trades between two CPUs over the season."""
    user = rs.user_team(run.final).id
    return sum(o.status == "accepted" and user not in (o.from_team, o.to_team) for o in run.final.offers)


def cpu_moves_per_week(run: Run) -> float:
    """CPU adds and awarded claims per week played."""
    user = rs.user_team(run.final).id
    moves = [e for e in run.final.events if e.kind in ("add", "claim") and e.team_id != user]
    return len(moves) / run.settings.end_week


# ---------------- the orchestrator's own rules ----------------

def test_create_game_makes_the_cpu_picks_up_to_the_users_first_turn():
    settings = league(12, slot=5)

    state = rg.create_game(settings, DATA, seed=1)

    assert (state.phase, state.week, len(state.picks)) == ("draft", 0, 4)
    assert replay_draft.on_the_clock(state, settings) == rs.user_team(state).id == 5


@pytest.mark.parametrize("settings, message", [
    (fakes.fake_settings(regular_season_weeks=16), "too long"),
    (fakes.fake_settings(scoring="ppr"), "ppr scoring"),
    (fakes.fake_settings(playoff_teams=6), "4-team playoff"),
])
def test_create_game_refuses_a_league_the_season_cannot_hold(settings, message):
    with pytest.raises(ValueError, match=message):
        rg.create_game(settings, DATA, seed=1)


def test_a_user_pick_is_followed_by_the_cpu_picks_up_to_the_next_turn():
    settings = league(12, slot=3)
    state = rg.create_game(settings, DATA, seed=1)
    wanted = replay_draft.available(state, DATA)[0].player_id

    after = rg.pick(state, settings, DATA, 1, wanted)

    assert wanted in rs.user_team(after).roster
    assert len(after.picks) == 21 and replay_draft.on_the_clock(after, settings) == 3      # snake: 3rd, then 22nd
    with pytest.raises(ValueError, match="already been drafted"):
        rg.pick(after, settings, DATA, 1, wanted)


def test_the_finished_draft_opens_week_one_with_the_cpus_start_of_week_done():
    run = simulate(*IDLE[0])

    drafted = run.steps[run.index("auto_draft", 0)].state

    assert (drafted.phase, drafted.week, drafted.locked) == ("open", 1, ())
    assert all(len(team.roster) == LEAGUES[0].roster_size for team in drafted.teams)
    assert len(drafted.schedule) == LEAGUES[0].regular_season_weeks
    for action in (rg.pick, rg.auto_pick, rg.auto_draft):
        with pytest.raises(ValueError, match="draft is over"):
            action(drafted, LEAGUES[0], DATA, 1, *([1] if action is rg.pick else []))


def test_advance_refuses_a_step_that_does_not_fit_the_phase():
    weekly, always_open = LEAGUES[0], LEAGUES[2]
    wednesday, none = simulate(*IDLE[0]), simulate(*IDLE[2])
    drafting = rg.create_game(weekly, DATA, 1)
    open_week, waivers = wednesday.steps[1].state, wednesday.steps[2].state
    cases = [(drafting, weekly, "play", "Finish the draft"), (open_week, weekly, "sunday", "Unknown step"),
             (open_week, weekly, "wednesday", "already run"), (waivers, weekly, "play", "Run week 2's waivers"),
             (none.steps[1].state, always_open, "wednesday", "no weekly waiver run"),
             (wednesday.final, weekly, "play", "season is over")]

    for state, settings, step, message in cases:
        with pytest.raises(ValueError, match=message):
            rg.advance(state, settings, DATA, 1, step)
    with pytest.raises(ValueError, match="during the season"):
        rg.start_week(drafting, weekly, DATA, 1)


def test_the_week_flow_follows_the_waiver_mode():
    wednesday, none = simulate(*IDLE[0]), simulate(*IDLE[2])

    assert [(s.label, s.week, s.state.phase) for s in wednesday.steps[2:5]] == [
        ("play", 1, "waivers"), ("wednesday", 2, "open"), ("play", 2, "waivers")]
    assert [(s.label, s.week, s.state.phase) for s in none.steps[2:4]] == [("play", 1, "open"), ("play", 2, "open")]
    assert all(step.state.locked == () for run in (wednesday, none) for step in run.steps if step.state.phase == "open")


def test_the_user_can_answer_a_cpu_offer_until_it_no_longer_stands():
    settings = LEAGUES[0]
    state = next(step.state for step in simulate(*IDLE[0]).steps if replay_trades.offers_for_user(step.state))
    offer = replay_trades.offers_for_user(state)[0]

    accepted = rs.user_team(rg.respond_offer(state, settings, DATA, offer.id, True)).roster
    declined = rg.respond_offer(state, settings, DATA, offer.id, False)
    without_him = rg.drop(state, settings, DATA, offer.get[0])

    assert set(offer.give) <= set(accepted) and not set(offer.get) & set(accepted)
    assert [o.status for o in declined.offers if o.id == offer.id] == ["declined"]
    assert rg.evaluate_trade(state, settings, DATA, offer.from_team, offer.get, offer.give).accepted
    with pytest.raises(rg.OfferExpired, match="no longer stands") as refused:
        rg.respond_offer(without_him, settings, DATA, offer.id, True)
    assert [o.status for o in refused.value.state.offers if o.id == offer.id] == ["expired"]


def test_cpus_make_their_ir_moves_first_at_the_waiver_run_and_at_the_start_of_the_week(monkeypatch):
    waivers = simulate(*IDLE[0]).steps[2].state
    calls = []
    for name in ("cpu_ir", "cpu_claims", "cpu_free_agency"):
        original = getattr(rg.replay_waiver_ai, name)
        monkeypatch.setattr(rg.replay_waiver_ai, name,
                            lambda *args, _name=name, _call=original: calls.append((_name, args[2])) or _call(*args))

    rg.advance(waivers, LEAGUES[0], DATA, 1, rg.WEDNESDAY)

    assert waivers.phase == "waivers" and [name for name, _ in calls] == [
        "cpu_ir", "cpu_claims", "cpu_ir", "cpu_free_agency"]
    assert calls[0][1] is calls[1][1]                        # the same Knowledge


def test_the_users_ir_actions_pass_through_to_the_users_team():
    settings, run = LEAGUES[0], simulate(*IDLE[0])
    state = next(step.state for step in run.steps if (step.state.week, step.state.phase) == (6, "open"))
    first, second, third = rs.user_team(state).roster[:3]
    data = fakes.tagged(DATA, {(first, 6): fakes.Injury("O"), (second, 6): fakes.Injury("IR")})

    placed = rg.ir_place(state, settings, data, first)
    back = rg.ir_activate(placed, settings, data, first)
    dropping = rg.ir_activate(placed, settings, data, first, drop=third)
    swapped = rg.ir_activate(placed, settings, data, first, swap=second)

    assert rs.user_team(placed).ir == (first,) and first not in rs.user_team(placed).roster
    assert rs.user_team(back).ir == () and first in rs.user_team(back).roster
    assert third in dropping.locked and third not in rs.held(rs.user_team(dropping))
    assert rs.user_team(swapped).ir == (second,) and first in rs.user_team(swapped).roster
    assert [e.kind for e in swapped.events[len(state.events):]] == ["ir", "ir"]
    with pytest.raises(ValueError, match="can't go on IR"):
        rg.ir_place(state, settings, DATA, first)               # no tag in the real week 6


def test_nothing_can_be_changed_once_the_season_is_over():
    settings, final = LEAGUES[0], simulate(*IDLE[0]).final
    mine, theirs = rs.user_team(final).roster, final.teams[0].roster
    free = replay_waivers.available_players(final, rg.knowledge(final, settings, DATA))[0].player_id
    actions = [lambda: rg.set_lineup(final, settings, DATA, {}),
               lambda: rg.submit_claim(final, settings, DATA, free, mine[0]),
               lambda: rg.add_drop(final, settings, DATA, free, mine[0]),
               lambda: rg.drop(final, settings, DATA, mine[0]),
               lambda: rg.propose_trade(final, settings, DATA, final.teams[0].id, mine[:1], theirs[:1]),
               lambda: rg.evaluate_trade(final, settings, DATA, final.teams[0].id, mine[:1], theirs[:1])]

    for action in actions:
        with pytest.raises(ValueError):
            action()


def test_engine_calls_never_receive_the_future(monkeypatch):
    """Structural no-hindsight: wrap every engine function the orchestrator can reach and look at what it
    is handed. Only play_week may hold the full season."""
    seen: list[tuple[str, int | None]] = []

    def watched(name: str, function):
        def call(*args, **kwargs):
            week = next((arg.week for arg in args if isinstance(arg, rs.State)), None)
            for arg in (*args, *kwargs.values()) if week is not None else ():
                if isinstance(arg, rv.Knowledge):
                    assert arg.week == week, name
                elif isinstance(arg, type(DATA)):
                    seen.append((name, arg.known_before))
                    assert name == "play_week" or arg.known_before == week, name
            return function(*args, **kwargs)
        return call

    for module in (rg.replay_draft, rg.replay_season, rg.replay_waivers, rg.replay_waiver_ai, rg.replay_trade_ai,
                   rg.replay_trades, rg.replay_ir):
        for name, function in list(vars(module).items()):
            if callable(function) and getattr(function, "__module__", "") == module.__name__ and name[0].islower():
                monkeypatch.setattr(module, name, watched(name, function))

    play(league(8, "wednesday", slot=2), seed=5, active=True, last_week=3)

    assert {known for name, known in seen if name == "play_week"} == {None}
    assert {name for name, known in seen} >= {"play_week", "run_cpu_picks", "make_pick"}


# ---------------- whole seasons ----------------

@pytest.mark.parametrize("settings, seed, active", IDLE + ACTIVE)
def test_a_whole_season_keeps_every_invariant_and_crowns_a_champion(settings, seed, active):
    run = simulate(settings, seed, active)

    final = run.final

    assert run.faults == ()
    assert final.phase == "complete" and final.champion in {team.id for team in final.teams}
    assert (final.week, len(final.results)) == (settings.end_week, settings.end_week)
    assert all(len(result.scores) == settings.teams and min(result.scores.values()) >= 0 for result in final.results)
    assert sum(event.kind == "champion" for event in final.events) == 1


def test_the_active_user_really_used_every_action():
    events = [event for settings, seed, active in ACTIVE for event in simulate(settings, seed, active).final.events]
    offers = [offer for settings, seed, active in ACTIVE for offer in simulate(settings, seed, active).final.offers]
    users = {settings.draft_slot for settings in LEAGUES}

    assert any(e.kind == "claim" and len(e.player_ids) == 2 and e.text.startswith("Your Team") for e in events)
    assert any(e.kind == "claim" and "failed" in e.text for e in events)
    assert any(e.kind == "add" and e.text.startswith("Your Team") for e in events)
    assert {o.status for o in offers if o.from_team in users and o.to_team not in users} >= {"accepted", "declined"}
    mine = [e.text for e in events if e.kind == "ir" and e.text.startswith("Your Team")]
    assert any(text.endswith(" to IR") and " activated " not in text for text in mine)          # placed
    assert any(" from IR, dropping " in text for text in mine)                                  # activated, drop
    assert any(" from IR and moved " in text for text in mine)                                  # swapped


@pytest.mark.parametrize("settings, seed, active", IDLE)
def test_a_cpu_keeps_its_injured_star_on_ir_while_he_is_out_and_starts_him_when_he_is_back(settings, seed, active):
    """IR-22, and IR-25 for an idle user who drafted him."""
    run, star = simulate(settings, seed, active), fakes.INJURED_STAR_ID
    before = {week: run.steps[run.index("play", week) - 1].state for week in range(1, settings.end_week + 1)}
    never = {fakes.NEVER_PLAYED_ID, fakes.IR_KICKER_ID}

    for week, state in before.items():
        owner = next((team for team in state.teams if star in rs.held(team)), None)
        assert not never & {pid for team in state.teams for pid in team.ir}
        if owner is None or owner.is_user:
            assert all(team.ir == () for team in state.teams if team.is_user)
            continue
        assert (star in owner.ir) == (week in fakes.INJURED_WEEKS), week
        if week in fakes.INJURED_WEEKS:                                   # the spot he opened was filled
            assert len(owner.roster) == settings.roster_size, week
        if week == fakes.STAR_BACK_WEEK:
            know = rg.knowledge(state, settings, DATA)
            lineup = replay_season.starters_for(state, settings, know, owner.id)
            assert star in owner.roster and star in {pid for ids in lineup.values() for pid in ids}


@pytest.mark.parametrize("settings, seed, active", [IDLE[0], ACTIVE[2]])
def test_the_same_seed_plays_the_same_game(settings, seed, active):
    first = simulate(settings, seed, active)

    again = play(settings, seed, active, checked=False)

    assert [rs.to_json(step.state) for step in again.steps] == [rs.to_json(step.state) for step in first.steps]
    other = play(settings, seed + 1, active, last_week=1, checked=False)
    assert [pick.player_id for pick in other.final.picks] != [pick.player_id for pick in first.final.picks]


@pytest.mark.parametrize("config, weeks", [(ACTIVE[0], (2, 7, 12)), (ACTIVE[1], (1, 5, 14))])
def test_no_decision_up_to_a_week_depends_on_anything_after_it(config, weeks):
    full = simulate(*config)
    known = [rs.to_json(step.state) for step in full.steps]

    for week in weeks:
        probe = play(*config, changed_after=week, last_week=week + 1, checked=False)
        cut = full.index("play", week)                # every roster, claim, trade and lineup before week's games

        assert [rs.to_json(step.state) for step in probe.steps[:cut]] == known[:cut]
        assert probe.final.results[:week] == full.final.results[:week]
        assert probe.final.results[week] != full.final.results[week]         # the probe really changed the future


# ---------------- are the outcomes believable? ----------------

def test_cpus_trade_with_each_other_a_believable_number_of_times():
    counts = [cpu_trades(simulate(*config)) for config in IDLE + ACTIVE]

    assert all(0 <= count <= 12 for count in counts), counts
    assert 2 <= mean(counts) <= 8, counts


def test_cpu_waiver_and_free_agent_moves_stay_modest():
    per_week = [cpu_moves_per_week(simulate(*config)) for config in IDLE + ACTIVE]

    assert all(0.1 <= rate / (config[0].teams - 1) <= 1.0 for rate, config in zip(per_week, IDLE + ACTIVE)), per_week


def test_a_user_who_does_nothing_finishes_mid_pack_on_average():
    """Place as a share of the league (0 = champion, 1 = last): CPUs that only auto-draft and move on the
    wire should neither bury nor lose to an auto-drafted roster that is never touched."""
    shares = [(user_place(run) - 1) / (run.settings.teams - 1) for run in (simulate(*config) for config in IDLE)]

    assert 0.3 <= mean(shares) <= 0.8, shares
    assert min(shares) < 0.5 < max(shares), shares


def test_every_step_fits_the_request_budget():
    steps = [step for config in IDLE + ACTIVE for step in simulate(*config).steps]

    for label in ("create", "auto_draft", "pick", "user", "wednesday", "play"):
        seconds = [step.seconds for step in steps if step.label == label]
        assert median(seconds) < STEP_BUDGET_SECONDS, (label, median(seconds), max(seconds))
