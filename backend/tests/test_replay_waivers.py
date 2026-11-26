"""Replay League waiver rules and the user's roster moves (services/replay_waivers.py), on the fake league."""
import re
from dataclasses import replace

import pytest

from services import replay_state as rs, replay_waivers as waivers
from services.replay_state import Claim, State
from services.replay_values import Knowledge
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings()            # 12 teams, 15-man rosters, the user is team 1
USER = SETTINGS.draft_slot
FIRST, SECOND, THIRD = 12, 11, 10           # the first three teams in the fake league's waiver order


def league(phase: str = "waivers", week: int = 1) -> State:
    return replace(fakes.drafted_state(SETTINGS), phase=phase, week=week)


def know(week: int = 1) -> Knowledge:
    return fakes.fake_knowledge(week, SETTINGS)


def unrostered(state: State, position: str | None = None, count: int = 4) -> list[int]:
    """The best unrostered players (at one position), as ids."""
    return [v.player_id for v in waivers.available_players(state, know(state.week), position)][:count]


def roster(state: State, team_id: int) -> tuple[int, ...]:
    return rs.team(state, team_id).roster


def bench(state: State, team_id: int, position: str = "WR") -> list[int]:
    """That team's players at a position, worst last."""
    values = know(state.week).by_id
    return [pid for pid in roster(state, team_id) if values[pid].position == position]


def claim(number: int, team_id: int, add: int, drop: int | None = None) -> Claim:
    return Claim(id=900 + number, team_id=team_id, add=add, drop=drop)


# ---------------- status and the Players screen ----------------

def test_status_is_rostered_for_a_player_on_any_team_in_every_phase():
    owned = roster(league(), FIRST)[0]

    assert waivers.player_status(league("waivers"), owned) == "rostered"
    assert waivers.player_status(league("open"), owned) == "rostered"


def test_every_unrostered_player_is_on_waivers_before_the_waiver_run_and_a_free_agent_after():
    player = unrostered(league())[0]

    assert waivers.player_status(league("waivers"), player) == "waivers"
    assert waivers.player_status(league("open"), player) == "free_agent"


def test_a_dropped_player_stays_on_waivers_in_the_open_phase_until_locked_is_cleared():
    state = league("open")
    cut = roster(state, USER)[-1]

    dropped = waivers.drop(state, SETTINGS, know(), USER, cut)

    assert waivers.player_status(dropped, cut) == "waivers"
    assert waivers.player_status(replace(dropped, locked=()), cut) == "free_agent"


def test_available_players_lists_only_unrostered_players_best_first():
    state = league("open")

    found = waivers.available_players(state, know())

    ids = [v.player_id for v in found]
    assert not set(ids) & rs.rostered(state)
    assert len(ids) == len(know().values) - len(rs.rostered(state))
    assert ids == [v.player_id for v in know().values if v.player_id in set(ids)]


def test_available_players_filters_by_position_and_by_name_ignoring_case():
    state = league("open")
    target = waivers.available_players(state, know(), "K")[0]

    kickers = waivers.available_players(state, know(), position="K")
    named = waivers.available_players(state, know(), search=f"  {target.name.upper()} ")

    assert kickers and {v.position for v in kickers} == {"K"}
    assert [v.player_id for v in named] == [target.player_id]


def test_available_players_status_filter_separates_waivers_free_agents_and_rostered():
    state = league("open")
    cut = roster(state, USER)[-1]
    state = waivers.drop(state, SETTINGS, know(), USER, cut)

    on_waivers = waivers.available_players(state, know(), status="waivers")
    free_agents = waivers.available_players(state, know(), status="free_agent")
    owned = waivers.available_players(state, know(), status="rostered")
    everyone = waivers.available_players(state, know(), status="all")

    assert [v.player_id for v in on_waivers] == [cut]
    assert cut not in {v.player_id for v in free_agents}
    assert {v.player_id for v in owned} == rs.rostered(state)
    assert len(everyone) == len(know().values) == len(on_waivers) + len(free_agents) + len(owned)


@pytest.mark.parametrize("bad", [{"position": "LB"}, {"status": "injured"}])
def test_available_players_rejects_unknown_filters(bad):
    with pytest.raises(ValueError, match="Unknown"):
        waivers.available_players(league(), know(), **bad)


# ---------------- the user's claims ----------------

def test_submit_claim_appends_the_users_claim_with_a_fresh_id():
    state = league()
    add, other = unrostered(state)[:2]
    cut = roster(state, USER)[-1]

    once = waivers.submit_claim(state, SETTINGS, know(), add, cut)
    twice = waivers.submit_claim(once, SETTINGS, know(), other, cut)       # the same drop is allowed

    assert once.claims == (Claim(state.next_id, USER, add, cut),)
    assert [c.add for c in twice.claims] == [add, other]
    assert twice.next_id == state.next_id + 2
    assert state.claims == ()                                             # the input is untouched


def test_submit_claim_is_refused_once_waivers_have_run():
    state = league("open")

    with pytest.raises(ValueError, match="before the weekly waiver run"):
        waivers.submit_claim(state, SETTINGS, know(), unrostered(state)[0], roster(state, USER)[-1])


def test_submit_claim_rejects_a_rostered_or_unknown_player():
    state = league()
    cut = roster(state, USER)[-1]

    with pytest.raises(ValueError, match=r"already on a roster \(CPU 12\)"):
        waivers.submit_claim(state, SETTINGS, know(), roster(state, FIRST)[0], cut)
    with pytest.raises(ValueError, match="not in this season's player pool"):
        waivers.submit_claim(state, SETTINGS, know(), 999_999, cut)


def test_submit_claim_needs_a_drop_from_the_users_own_roster_when_the_roster_is_full():
    state = league()
    add = unrostered(state)[0]

    with pytest.raises(ValueError, match="roster is full"):
        waivers.submit_claim(state, SETTINGS, know(), add, None)
    with pytest.raises(ValueError, match="You can't drop .* he isn't on that roster"):
        waivers.submit_claim(state, SETTINGS, know(), add, roster(state, FIRST)[0])


def test_submit_claim_allows_no_drop_while_the_roster_has_room():
    state = waivers.drop(league(), SETTINGS, know(), USER, roster(league(), USER)[-1])

    claimed = waivers.submit_claim(state, SETTINGS, know(), unrostered(state)[0], None)

    assert claimed.claims[0].drop is None


def test_submit_claim_rejects_a_second_claim_for_the_same_player():
    state = league()
    add = unrostered(state)[0]
    first, second = roster(state, USER)[-2:]
    claimed = waivers.submit_claim(state, SETTINGS, know(), add, first)

    with pytest.raises(ValueError, match="already have a claim"):
        waivers.submit_claim(claimed, SETTINGS, know(), add, second)


def test_cancel_claim_removes_only_that_claim_and_rejects_an_unknown_id():
    state = league()
    cut = roster(state, USER)[-1]
    for add in unrostered(state)[:2]:
        state = waivers.submit_claim(state, SETTINGS, know(), add, cut)
    keep, gone = state.claims

    cancelled = waivers.cancel_claim(state, gone.id)

    assert cancelled.claims == (keep,)
    with pytest.raises(ValueError, match="no pending claim"):
        waivers.cancel_claim(cancelled, gone.id)


def test_reorder_claims_sets_the_preference_order_and_needs_every_id_exactly_once():
    state = league()
    cut = roster(state, USER)[-1]
    for add in unrostered(state)[:3]:
        state = waivers.submit_claim(state, SETTINGS, know(), add, cut)
    a, b, c = (claim_.id for claim_ in state.claims)

    reordered = waivers.reorder_claims(state, [c, a, b])

    assert [claim_.id for claim_ in reordered.claims] == [c, a, b]
    for bad in ([a, b], [a, b, b], [a, b, c, 12345]):
        with pytest.raises(ValueError, match="every pending claim exactly once"):
            waivers.reorder_claims(state, bad)


# ---------------- the waiver run ----------------

def test_claims_are_awarded_in_priority_order_and_each_winner_moves_to_the_back():
    state = league()
    a, b, c = unrostered(state, "WR")[:3]
    claims = {THIRD: [claim(1, THIRD, c, bench(state, THIRD)[-1])],
              FIRST: [claim(2, FIRST, a, bench(state, FIRST)[-1])],
              SECOND: [claim(3, SECOND, b, bench(state, SECOND)[-1])]}

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    assert a in roster(after, FIRST) and b in roster(after, SECOND) and c in roster(after, THIRD)
    assert after.waiver_order == (9, 8, 7, 6, 5, 4, 3, 2, 1, FIRST, SECOND, THIRD)
    assert [e.team_id for e in after.events if e.kind == "claim"] == [FIRST, SECOND, THIRD]
    assert after.phase == "waivers" and state.waiver_order[0] == FIRST      # no phase change, input untouched


def test_a_contested_player_goes_to_the_team_with_the_higher_priority():
    state = league()
    prize, consolation = unrostered(state, "WR")[:2]
    claims = {SECOND: [claim(1, SECOND, prize, bench(state, SECOND)[-1]),
                       claim(2, SECOND, consolation, bench(state, SECOND)[-1])],
              FIRST: [claim(3, FIRST, prize, bench(state, FIRST)[-1])]}

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    assert prize in roster(after, FIRST)
    assert consolation in roster(after, SECOND) and prize not in roster(after, SECOND)
    assert after.waiver_order[-2:] == (FIRST, SECOND)


def test_a_team_with_two_claims_waits_at_the_back_of_the_line_for_its_second():
    state = league()
    a, b = unrostered(state, "WR")[:2]
    spare_one, spare_two = bench(state, FIRST)[-2:]
    claims = {FIRST: [claim(1, FIRST, a, spare_one), claim(2, FIRST, b, spare_two)],
              SECOND: [claim(3, SECOND, b, bench(state, SECOND)[-1])]}

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    assert a in roster(after, FIRST) and b in roster(after, SECOND)
    assert spare_two in roster(after, FIRST)                 # its second claim was skipped: nobody dropped
    assert after.waiver_order[-2:] == (FIRST, SECOND)


def test_a_team_can_win_twice_when_nobody_else_wants_its_second_player():
    state = league()
    a, b = unrostered(state, "WR")[:2]
    spare_one, spare_two = bench(state, FIRST)[-2:]
    claims = {FIRST: [claim(1, FIRST, a, spare_one), claim(2, FIRST, b, spare_two)]}

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    assert {a, b} <= set(roster(after, FIRST)) and len(roster(after, FIRST)) == SETTINGS.roster_size
    assert after.locked == (spare_one, spare_two)
    assert after.waiver_order[-1] == FIRST


def test_a_claim_whose_drop_is_already_gone_is_skipped_without_costing_priority():
    state = league()
    a, b, c = unrostered(state, "WR")[:3]
    spare = bench(state, FIRST)[-1]
    claims = {FIRST: [claim(1, FIRST, a, spare), claim(2, FIRST, b, spare)],
              SECOND: [claim(3, SECOND, c, 999_999)]}        # a drop that was never on the roster

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    assert a in roster(after, FIRST) and b not in rs.rostered(after) and c not in rs.rostered(after)
    assert roster(after, SECOND) == roster(state, SECOND)
    assert after.waiver_order == (*(t for t in state.waiver_order if t != FIRST), FIRST)


def test_a_cpu_claim_that_would_leave_no_legal_lineup_is_skipped_but_the_user_may_do_it():
    state = league()
    add = unrostered(state, "WR")[0]
    cpu_kicker, user_kicker = bench(state, FIRST, "K")[0], bench(state, USER, "K")[0]
    state = waivers.submit_claim(state, SETTINGS, know(), add, user_kicker)

    after = waivers.process_claims(state, SETTINGS, know(), {FIRST: [claim(1, FIRST, add, cpu_kicker)]})

    assert cpu_kicker in roster(after, FIRST) and add not in roster(after, FIRST)
    assert add in roster(after, USER) and user_kicker not in roster(after, USER)


def test_a_successful_user_claim_is_logged_with_both_players_and_clears_the_claims():
    state = league()
    add = unrostered(state)[0]
    cut = roster(state, USER)[-1]
    state = waivers.submit_claim(state, SETTINGS, know(), add, cut)

    after = waivers.process_claims(state, SETTINGS, know(), {})

    kinds = [(e.kind, e.team_id, e.player_ids) for e in after.events]
    names = know().by_id
    assert kinds == [("claim", USER, (add, cut))]                       # one event for the whole move
    assert after.events[-1].text == f"You claimed {names[add].name}, dropping {names[cut].name}"
    assert after.claims == () and after.locked == (cut,)
    assert add in roster(after, USER) and len(roster(after, USER)) == SETTINGS.roster_size
    assert after.waiver_order[-1] == USER


def test_a_failed_user_claim_is_logged_briefly_and_cpu_failures_are_not():
    state = league()
    prize = unrostered(state, "WR")[0]
    state = waivers.submit_claim(state, SETTINGS, know(), prize, roster(state, USER)[-1])
    claims = {FIRST: [claim(1, FIRST, prize, bench(state, FIRST)[-1])],
              SECOND: [claim(2, SECOND, prize, bench(state, SECOND)[-1])]}

    after = waivers.process_claims(state, SETTINGS, know(), claims)

    failures = [e for e in after.events if "failed" in e.text]
    assert [(e.kind, e.team_id, e.player_ids) for e in failures] == [("claim", USER, (prize,))]
    assert "is already on a roster (CPU 12)" in failures[0].text
    assert roster(after, USER) == roster(state, USER) and after.claims == ()
    assert after.waiver_order[-1] == FIRST and USER in after.waiver_order[:-1]     # the user kept their place


def test_claims_without_a_drop_stop_when_the_roster_is_full():
    state = waivers.drop(league(), SETTINGS, know(), USER, roster(league(), USER)[-1])
    first, second = unrostered(state)[:2]
    for add in (first, second):
        state = waivers.submit_claim(state, SETTINGS, know(), add, None)

    after = waivers.process_claims(state, SETTINGS, know(), {})

    assert first in roster(after, USER) and second not in roster(after, USER)
    assert len(roster(after, USER)) == SETTINGS.roster_size
    assert "roster is full" in after.events[-1].text


def test_the_users_claims_always_come_from_the_state_not_from_the_callers_mapping():
    state = league()
    mine, forged = unrostered(state)[:2]
    cut = roster(state, USER)[-1]
    state = waivers.submit_claim(state, SETTINGS, know(), mine, cut)

    after = waivers.process_claims(state, SETTINGS, know(), {USER: [claim(1, USER, forged, cut)]})

    assert mine in roster(after, USER) and forged not in rs.rostered(after)


# ---------------- free agency and drops ----------------

def test_add_drop_swaps_a_free_agent_for_the_dropped_player_who_goes_on_waivers():
    state = league("open")
    add = unrostered(state)[0]
    cut = roster(state, USER)[-1]

    after = waivers.add_drop(state, SETTINGS, know(), USER, add, cut)

    assert add in roster(after, USER) and cut not in roster(after, USER)
    assert after.locked == (cut,) and after.waiver_order == state.waiver_order
    assert [(e.kind, e.player_ids) for e in after.events] == [("add", (add, cut))]     # one event, not two
    assert roster(state, USER)[-1] == cut                               # the input is untouched


def test_add_drop_is_refused_before_the_waiver_run():
    state = league("waivers")

    with pytest.raises(ValueError, match="put in a waiver claim"):
        waivers.add_drop(state, SETTINGS, know(), USER, unrostered(state)[0], roster(state, USER)[-1])


def test_a_dropped_player_cannot_be_added_again_until_locked_is_cleared():
    state = league("open")
    cut, other = roster(state, USER)[-2:]
    state = waivers.drop(state, SETTINGS, know(), USER, cut)

    with pytest.raises(ValueError, match="on waivers until next week"):
        waivers.add_drop(state, SETTINGS, know(), FIRST, cut, bench(state, FIRST)[-1])
    with pytest.raises(ValueError, match="on waivers until next week"):
        waivers.add_drop(state, SETTINGS, know(), USER, cut, other)

    next_week = replace(state, locked=())
    assert cut in roster(waivers.add_drop(next_week, SETTINGS, know(), USER, cut), USER)


def test_add_drop_enforces_the_roster_size_and_whose_players_they_are():
    state = league("open")
    add = unrostered(state)[0]

    with pytest.raises(ValueError, match=r"roster is full \(15 players\)"):
        waivers.add_drop(state, SETTINGS, know(), USER, add)
    with pytest.raises(ValueError, match="You can't drop .* he isn't on that roster"):
        waivers.add_drop(state, SETTINGS, know(), USER, add, roster(state, FIRST)[0])
    with pytest.raises(ValueError, match=r"already on a roster \(CPU 12\)"):
        waivers.add_drop(state, SETTINGS, know(), USER, roster(state, FIRST)[0], roster(state, USER)[-1])
    with pytest.raises(ValueError, match="not in this season's player pool"):
        waivers.add_drop(state, SETTINGS, know(), USER, 999_999, roster(state, USER)[-1])
    with pytest.raises(ValueError, match="No team 99"):
        waivers.add_drop(state, SETTINGS, know(), 99, add)


def test_the_user_may_drop_their_only_kicker_for_a_receiver():
    state = league("open")
    kicker = bench(state, USER, "K")[0]

    after = waivers.add_drop(state, SETTINGS, know(), USER, unrostered(state, "WR")[0], kicker)

    assert not bench(after, USER, "K")


def test_drop_releases_a_player_and_leaves_the_spot_open():
    state = league("open")
    cut = roster(state, USER)[0]

    after = waivers.drop(state, SETTINGS, know(), USER, cut)

    assert len(roster(after, USER)) == SETTINGS.roster_size - 1 and after.locked == (cut,)
    assert after.events[-1].text == f"You dropped {know().by_id[cut].name}"


def test_drop_rejects_another_teams_player_and_a_league_that_is_not_in_season():
    state = league("open")

    with pytest.raises(ValueError, match="isn't on the roster"):
        waivers.drop(state, SETTINGS, know(), USER, roster(state, FIRST)[0])
    with pytest.raises(ValueError, match="during the season"):
        waivers.drop(replace(state, phase="complete"), SETTINGS, know(), USER, roster(state, USER)[0])


# ---------------- no hindsight ----------------

def test_the_waiver_run_and_the_players_screen_do_not_depend_on_the_future():
    week = 6
    state = league(week=week)
    changed = fakes.fake_knowledge(week, SETTINGS, fakes.future_changed(fakes.fake_season(), after_week=week))
    prize, other = unrostered(state, "RB")[:2]
    state = waivers.submit_claim(state, SETTINGS, know(week), prize, roster(state, USER)[-1])
    claims = {FIRST: [claim(1, FIRST, prize, bench(state, FIRST)[-1])],
              SECOND: [claim(2, SECOND, other, bench(state, SECOND)[-1])]}

    real = waivers.process_claims(state, SETTINGS, know(week), claims)
    blind = waivers.process_claims(state, SETTINGS, changed, claims)

    assert rs.to_json(real) == rs.to_json(blind)
    assert waivers.available_players(real, know(week)) == waivers.available_players(blind, changed)


# ---------------- the IR slot ----------------

def to_ir(state: State, team_id: int, player_id: int) -> State:
    """He moves from the active roster to IR (no tag check: an untagged IR player blocks his team)."""
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p != player_id),
                                       ir=(*team.ir, player_id)))


def blocked_sentence(team_id: int, player_id: int) -> str:
    name = know().by_id[player_id].name
    if rs.team(league(), team_id).is_user:
        return f"Activate {name} from IR to trade or claim: he no longer has an O, IR or PUP tag (or drop him)."
    return (f"{rs.team(league(), team_id).name} can't add, claim or trade for players while "
            f"{name} is on IR without an O, IR or PUP tag: activate or drop him first.")


def test_an_ir_player_is_rostered_and_not_available():
    hurt = roster(league(), SECOND)[0]
    state = to_ir(league("open"), SECOND, hurt)

    assert waivers.player_status(state, hurt) == "rostered"
    assert hurt not in [v.player_id for v in waivers.available_players(state, know())]
    assert hurt in [v.player_id for v in waivers.available_players(state, know(), status="rostered")]
    with pytest.raises(ValueError, match=r"is already on a roster \(CPU 11\)"):
        waivers.add_drop(state, SETTINGS, know(), USER, hurt, roster(state, USER)[-1])


def test_a_blocked_team_cant_add_or_claim():
    stale = roster(league(), USER)[-1]
    sentence = blocked_sentence(USER, stale)
    target = unrostered(league())[0]

    with pytest.raises(ValueError, match="^" + re.escape(sentence) + "$"):
        waivers.add_drop(to_ir(league("open"), USER, stale), SETTINGS, know(), USER, target)
    with pytest.raises(ValueError, match="^" + re.escape(sentence) + "$"):
        waivers.submit_claim(to_ir(league("waivers"), USER, stale), SETTINGS, know(), target, None)


def test_a_claim_of_a_team_blocked_by_the_run_fails_with_the_sentence():
    stale = roster(league(), USER)[-1]
    target, other = unrostered(league())[:2]
    state = waivers.submit_claim(league(), SETTINGS, know(), target, roster(league(), USER)[0])
    state = to_ir(to_ir(state, USER, stale), SECOND, roster(state, SECOND)[0])
    cpu = {SECOND: [claim(1, SECOND, other, bench(state, SECOND)[-1])]}

    after = waivers.process_claims(state, SETTINGS, know(), cpu)

    assert target not in rs.rostered(after) and other not in rs.rostered(after) and after.claims == ()
    failures = [e.text for e in after.events if e.kind == "claim"]
    assert failures == [f"Your claim for {know().by_id[target].name} failed. {blocked_sentence(USER, stale)}"]


def test_an_ir_player_cant_be_the_drop_of_a_claim_but_can_be_dropped():
    hurt = roster(league(), USER)[0]
    state = to_ir(league("waivers"), USER, hurt)
    tagged = fakes.fake_knowledge(1, SETTINGS, fakes.tagged(fakes.fake_season(), {(hurt, 1): fakes.Injury("O")}))

    with pytest.raises(ValueError, match=f"can't drop {tagged.by_id[hurt].name}: he isn't on that roster"):
        waivers.submit_claim(state, SETTINGS, tagged, unrostered(state)[0], hurt)
    assert waivers.submit_claim(state, SETTINGS, tagged, unrostered(state)[0], None).claims    # IR is no roster spot
    dropped = waivers.drop(state, SETTINGS, know(), USER, hurt)
    assert rs.team(dropped, USER).ir == () and hurt in dropped.locked
    assert dropped.events[-1].kind == "drop"


def test_a_pending_claim_fails_at_the_run_when_its_drop_has_gone_to_ir():
    state = league()
    target, hurt = unrostered(state)[0], roster(state, USER)[-1]
    tagged = fakes.fake_knowledge(1, SETTINGS, fakes.tagged(fakes.fake_season(), {(hurt, 1): fakes.Injury("O")}))
    claimed = to_ir(waivers.submit_claim(state, SETTINGS, tagged, target, hurt), USER, hurt)           # IR-26

    after = waivers.process_claims(claimed, SETTINGS, tagged, {})

    assert target not in rs.rostered(after) and rs.team(after, USER).ir == (hurt,)
    assert [e.text for e in after.events if e.kind == "claim"] == [
        f"Your claim for {tagged.by_id[target].name} failed. "
        f"{rs.team(state, USER).name} can't drop {tagged.by_id[hurt].name}: he isn't on that roster."]
