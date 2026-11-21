"""Replay League IR slot (services/replay_ir.py): placing, activating, swapping and the block, on the fake league."""
from dataclasses import replace

import pytest

from services import replay_ir as ir, replay_state as rs, replay_waivers as waivers
from services.replay_state import State
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings()
STAR = fakes.INJURED_STAR_ID
BACK = fakes.STAR_BACK_WEEK


def league(week: int = 7, phase: str = "open") -> State:
    return replace(fakes.drafted_state(SETTINGS), phase=phase, week=week)


def know(week: int = 7, data=None):
    return fakes.fake_knowledge(week, SETTINGS, data)


def owner(state: State, player_id: int) -> rs.Team:
    return next(t for t in state.teams if player_id in rs.held(t))


OWNER = owner(league(), STAR)
MATE = next(pid for pid in OWNER.roster if pid != STAR and know().by_id[pid].position == "WR")   # a healthy teammate
STRANGER = next(pid for t in league().teams if t.id != OWNER.id for pid in t.roster)            # on another team


def name(player_id: int) -> str:
    return know().by_id[player_id].name


def with_tag(tag: str, player_id: int = MATE, weeks=range(1, 18)):
    return fakes.tagged(fakes.fake_season(), {(player_id, w): fakes.Injury(tag) for w in weeks})


def check_invariants(state: State) -> None:
    held = [pid for t in state.teams for pid in rs.held(t)]
    assert len(held) == len(set(held))
    assert all(len(t.ir) <= ir.IR_SLOTS and not set(t.ir) & set(t.roster) for t in state.teams)


def placed(week: int = 7, k=None) -> State:
    """The star's owner has put him on IR in `week` (he is tagged O in weeks 6-9)."""
    return ir.place(league(week), k or know(week), OWNER.id, STAR)


# ---------------- eligibility ----------------

@pytest.mark.parametrize("week, expected", [(5, False), (6, True), (9, True), (BACK, False)])
def test_only_an_o_ir_or_pup_tag_this_week_makes_a_player_eligible(week, expected):
    assert ir.eligible(know(week), STAR) is expected


@pytest.mark.parametrize("tag, expected", [("O", True), ("IR", True), ("PUP", True), ("Q", False), ("D", False),
                                           ("SUS", False)])
def test_eligibility_by_tag(tag, expected):
    assert ir.eligible(know(7, with_tag(tag)), MATE) is expected


def test_no_projection_without_a_tag_never_qualifies_and_nobody_is_eligible_at_week_0():
    assert know(7).by_id[fakes.NEVER_PLAYED_ID].out
    assert not ir.eligible(know(7), fakes.NEVER_PLAYED_ID)
    assert not ir.eligible(know(0, with_tag("IR", weeks=[1])), MATE)
    assert not ir.eligible(know(7), 999_999)


def test_the_ir_kicker_is_eligible_from_his_tag():
    assert not ir.eligible(know(8), fakes.IR_KICKER_ID) and ir.eligible(know(9), fakes.IR_KICKER_ID)


def test_a_hard_tag_carried_over_a_bye_keeps_him_eligible():      # IR-16: the star rests in week 12
    data = fakes.tagged(fakes.fake_season(), {(STAR, 12): fakes.Injury("IR")})
    assert fakes.fake_season().on_bye(STAR, 12)
    assert ir.eligible(know(12, data), STAR)


# ---------------- placing ----------------

def test_place_moves_him_from_the_roster_to_ir_and_changes_nothing_else():
    before = replace(league(), lineup={"RB": (STAR,)})
    after = ir.place(before, know(), OWNER.id, STAR)
    team = rs.team(after, OWNER.id)

    assert STAR not in team.roster and team.ir == (STAR,)
    assert len(team.roster) == len(OWNER.roster) - 1 and SETTINGS.roster_size == len(OWNER.roster)   # IR-6
    assert STAR in rs.rostered(after) and rs.held(team) == (*team.roster, STAR)
    event = after.events[-1]
    assert (event.kind, event.text, event.player_ids, event.team_id) == (
        "ir", f"{OWNER.name} moved {name(STAR)} to IR", (STAR,), OWNER.id)
    assert len(after.events) == len(before.events) + 1
    assert (after.locked, after.traded, after.claims, after.lineup) == (
        before.locked, before.traded, before.claims, before.lineup)
    check_invariants(after)


@pytest.mark.parametrize("phase", ["draft", "complete"])
def test_ir_moves_only_happen_during_the_season(phase):
    message = "Players can only be moved to or from IR during the season."
    with pytest.raises(ValueError, match=f"^{message}$"):
        ir.place(league(phase=phase), know(), OWNER.id, STAR)
    with pytest.raises(ValueError, match=f"^{message}$"):
        ir.activate(replace(placed(), phase=phase), SETTINGS, know(), OWNER.id, STAR)


def test_placing_works_in_the_waiver_phase():
    assert rs.team(ir.place(league(phase="waivers"), know(), OWNER.id, STAR), OWNER.id).ir == (STAR,)


def test_place_refusals():
    with pytest.raises(ValueError, match="^That player is not in this season's player pool.$"):
        ir.place(league(), know(), OWNER.id, 999_999)
    with pytest.raises(ValueError, match=f"^{name(STAR)} is already on IR.$"):
        ir.place(placed(), know(), OWNER.id, STAR)
    with pytest.raises(ValueError, match=f"^{name(STRANGER)} is not on {OWNER.name}'s roster.$"):
        ir.place(league(), know(), OWNER.id, STRANGER)


@pytest.mark.parametrize("tag", [None, "Q", "D", "SUS"])
def test_a_player_without_an_o_ir_or_pup_tag_cant_go_on_ir(tag):
    data = with_tag(tag) if tag else None
    message = f"^{name(MATE)} can't go on IR: only a player tagged O, IR or PUP this week can.$"
    with pytest.raises(ValueError, match=message):
        ir.place(league(), know(7, data), OWNER.id, MATE)


def test_never_played_and_week_zero_cant_go_on_ir():
    holder = owner(league(), fakes.NEVER_PLAYED_ID)
    with pytest.raises(ValueError, match="can't go on IR"):
        ir.place(league(), know(), holder.id, fakes.NEVER_PLAYED_ID)
    with pytest.raises(ValueError, match="can't go on IR"):
        ir.place(league(week=0), know(0, with_tag("O", weeks=[1])), OWNER.id, MATE)


def test_the_one_slot_stays_taken_even_by_a_player_who_lost_his_tag():
    data = with_tag("O", weeks=[BACK])
    state = replace(placed(), week=BACK)
    with pytest.raises(ValueError, match=f"^{OWNER.name}'s IR slot is taken by {name(STAR)}: activate or drop him first.$"):
        ir.place(state, know(BACK, data), OWNER.id, MATE)


# ---------------- activating ----------------

def test_activate_with_room_brings_him_back():
    after = ir.activate(placed(), SETTINGS, know(), OWNER.id, STAR)
    team = rs.team(after, OWNER.id)

    assert team.ir == () and team.roster[-1] == STAR and len(team.roster) == SETTINGS.roster_size
    event = after.events[-1]
    assert (event.kind, event.text, event.player_ids) == ("ir", f"{OWNER.name} activated {name(STAR)} from IR", (STAR,))
    check_invariants(after)


def test_a_still_injured_player_may_be_activated():
    assert ir.eligible(know(8), STAR)
    assert STAR in rs.team(ir.activate(placed(8), SETTINGS, know(8), OWNER.id, STAR), OWNER.id).roster


def full(state: State) -> State:
    """The owner's open spot filled with a free agent."""
    team = rs.team(state, OWNER.id)
    extra = waivers.available_players(state, know())[0].player_id
    return rs.with_team(state, replace(team, roster=(*team.roster, extra)))


def test_a_full_roster_needs_a_drop():
    message = rf"^The roster is full \({SETTINGS.roster_size} players\): pick a player to drop to activate {name(STAR)}.$"
    with pytest.raises(ValueError, match=message):
        ir.activate(full(placed()), SETTINGS, know(), OWNER.id, STAR)


def test_activate_with_a_drop_logs_the_drop_then_the_ir_move():
    before = full(placed())
    after = ir.activate(before, SETTINGS, know(), OWNER.id, STAR, drop=MATE)
    team = rs.team(after, OWNER.id)

    assert STAR in team.roster and MATE not in rs.held(team) and MATE in after.locked and team.ir == ()
    assert len(team.roster) == SETTINGS.roster_size
    drop, move = after.events[-2:]
    assert (drop.kind, drop.player_ids) == ("drop", (MATE,))
    assert (move.kind, move.text, move.player_ids) == (
        "ir", f"{OWNER.name} activated {name(STAR)} from IR, dropping {name(MATE)}", (STAR, MATE))
    check_invariants(after)


def test_a_drop_is_made_even_when_there_was_room():
    after = ir.activate(placed(), SETTINGS, know(), OWNER.id, STAR, drop=MATE)
    assert MATE in after.locked and len(rs.team(after, OWNER.id).roster) == SETTINGS.roster_size - 1


def test_activate_refusals_leave_the_state_as_it_was():
    state = full(placed())
    events = state.events
    with pytest.raises(ValueError, match=f"^{name(MATE)} is not on {OWNER.name}'s IR.$"):
        ir.activate(state, SETTINGS, know(), OWNER.id, MATE)
    with pytest.raises(ValueError, match="^Pick either a player to drop or a player to swap onto IR, not both.$"):
        ir.activate(state, SETTINGS, know(), OWNER.id, STAR, drop=MATE, swap=MATE)
    for bad in (STAR, STRANGER):        # the player himself (on IR) and another team's player
        with pytest.raises(ValueError, match=f"^{OWNER.name} can't drop {name(bad)}: he isn't on the active roster.$"):
            ir.activate(state, SETTINGS, know(), OWNER.id, STAR, drop=bad)
    assert state.events == events and rs.team(state, OWNER.id).ir == (STAR,)


# ---------------- the swap ----------------

def test_swap_trades_places_in_one_event_and_keeps_the_roster_size():
    data = with_tag("O")
    before = full(ir.place(league(), know(7, data), OWNER.id, STAR))
    after = ir.activate(before, SETTINGS, know(7, data), OWNER.id, STAR, swap=MATE)
    team = rs.team(after, OWNER.id)

    assert team.ir == (MATE,) and STAR in team.roster and MATE not in team.roster
    assert len(team.roster) == len(rs.team(before, OWNER.id).roster) == SETTINGS.roster_size   # no room check
    assert len(after.events) == len(before.events) + 1
    event = after.events[-1]
    assert (event.kind, event.text, event.player_ids) == (
        "ir", f"{OWNER.name} activated {name(STAR)} from IR and moved {name(MATE)} to IR", (STAR, MATE))
    assert after.locked == before.locked
    check_invariants(after)


@pytest.mark.parametrize("tag", [None, "Q", "SUS"])
def test_swap_needs_an_eligible_player(tag):
    data = with_tag(tag) if tag else None
    with pytest.raises(ValueError, match=f"^{name(MATE)} can't go on IR: only a player tagged O, IR or PUP"):
        ir.activate(placed(7, know(7, data)), SETTINGS, know(7, data), OWNER.id, STAR, swap=MATE)


def test_swap_needs_a_player_on_the_active_roster():
    with pytest.raises(ValueError, match=f"^{name(STRANGER)} is not on {OWNER.name}'s roster.$"):
        ir.activate(placed(), SETTINGS, know(), OWNER.id, STAR, swap=STRANGER)


# ---------------- blocked ----------------

def blocked_league() -> State:
    """The star was left on IR into the week he is back (tagged Q): his owner is blocked."""
    return replace(placed(9), week=BACK)


def test_a_team_whose_ir_player_lost_his_tag_is_blocked():
    state, k = blocked_league(), know(BACK)
    sentence = (f"{OWNER.name} can't add, claim or trade for players while {name(STAR)} is on IR without an O, IR "
                "or PUP tag: activate or drop him first.")

    assert ir.ineligible(state, k, OWNER.id) == (STAR,)
    assert ir.blocked_reason(state, k, OWNER.id) == sentence
    with pytest.raises(ValueError, match=r"^" + sentence.replace(".", r"\.") + "$"):
        ir.require_unblocked(state, k, OWNER.id)
    other = next(t.id for t in state.teams if t.id != OWNER.id)
    assert ir.blocked_reason(state, k, other) is None


def test_a_team_whose_ir_player_is_still_eligible_is_never_blocked():
    for week in (6, 7, 8, 9):
        state = replace(placed(6), week=week)
        assert ir.ineligible(state, know(week), OWNER.id) == ()
        assert ir.blocked_reason(state, know(week), OWNER.id) is None
        ir.require_unblocked(state, know(week), OWNER.id)


def test_activating_or_dropping_him_ends_the_block_at_once():
    state, k = blocked_league(), know(BACK)
    activated = ir.activate(state, SETTINGS, k, OWNER.id, STAR)
    dropped = rs.release(state, SETTINGS, OWNER.id, STAR, name(STAR))

    assert ir.blocked_reason(activated, k, OWNER.id) is None
    assert ir.blocked_reason(dropped, k, OWNER.id) is None and STAR in dropped.locked
    check_invariants(activated)
    check_invariants(dropped)
