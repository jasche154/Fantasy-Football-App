"""Replay League CPU waiver logic (services/replay_waiver_ai.py), on the fake league.

`season` plays the CPU side of twelve weeks (claims -> waiver run -> free agency -> next week) once for the
module; the behaviour tests read that log, the unit tests use one week of a freshly drafted league.
"""
import time
from unittest import mock
from dataclasses import dataclass, replace
from types import MappingProxyType

import pytest

from services import lineup_gain, replay_slots, replay_state as rs, replay_values as rv, replay_waiver_ai as ai
from services import replay_ir, replay_waivers
from services.replay_pool import SKILL
from services.replay_state import Claim, Event, State
from services.replay_values import Knowledge, ReplayValue
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings()            # 12 teams, 15-man rosters, the user is team 1
USER = SETTINGS.draft_slot
CPUS = tuple(t for t in range(1, SETTINGS.teams + 1) if t != USER)
SEED = 7
SIM_WEEKS = 12                              # through the byes (5-12) and the star's injury (6-9)
BUDGET_SECONDS = 0.5


@dataclass(frozen=True)
class Week:
    week: int
    know: Knowledge
    before: State                           # phase "waivers", nothing locked
    claims: dict[int, list[Claim]]
    after_claims: State
    after: State                            # after the free-agency pass

    @property
    def events(self) -> tuple[Event, ...]:
        return self.after.events[len(self.before.events):]


def league(week: int = 1, phase: str = "waivers") -> State:
    return replace(fakes.drafted_state(SETTINGS), phase=phase, week=week)


def know(week: int = 1) -> Knowledge:
    return fakes.fake_knowledge(week, SETTINGS)


def values(state: State, team_id: int, week: int) -> list[ReplayValue]:
    return [know(week).by_id[pid] for pid in rs.team(state, team_id).roster]


def without(state: State, team_id: int, *player_ids: int) -> State:
    """The state with those players simply gone from a roster (not dropped, so not locked)."""
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p not in player_ids)))


def at(state: State, team_id: int, position: str, week: int = 1) -> list[int]:
    return [v.player_id for v in values(state, team_id, week) if v.position == position]


def top_hundred(knowledge: Knowledge) -> set[int]:
    ranked = sorted((v for v in knowledge.values if v.healthy_value > 0), key=lambda v: -v.healthy_value)
    return {v.player_id for v in ranked[:ai.PROTECTED_RANK]}


@pytest.fixture(scope="module")
def season() -> tuple[Week, ...]:
    """The claim and free-agency rules over 12 weeks, without `_upgrade` (tested on its own below): its trade-value
    adds would take the breakout back in free agency the week the market notices him, before any claim."""
    state, weeks = fakes.drafted_state(SETTINGS), []
    with mock.patch.object(ai, "_upgrade", lambda *args: None):
        return _season(state, weeks)


def _season(state: State, weeks: list) -> tuple[Week, ...]:
    for week in range(1, SIM_WEEKS + 1):            # the order replay_game uses: IR, claims, IR, free agency
        before = replace(state, week=week, phase="waivers", locked=())
        knowledge = know(week)
        moved = ai.cpu_ir(before, SETTINGS, knowledge, SEED)
        claims = ai.cpu_claims(moved, SETTINGS, knowledge, SEED)
        after_claims = replay_waivers.process_claims(moved, SETTINGS, knowledge, claims)
        state = ai.cpu_free_agency(ai.cpu_ir(after_claims, SETTINGS, knowledge, SEED), SETTINGS, knowledge, SEED)
        weeks.append(Week(week, knowledge, before, claims, after_claims, state))
    return tuple(weeks)


# ---------------- the shared candidate list and who may be dropped ----------------

def test_candidates_are_a_short_list_of_unrostered_skill_players_with_the_most_valuable_first():
    state = league(6)
    free = replay_waivers.available_players(state, know(6))

    found = ai.candidates(know(6), free)

    ids = [v.player_id for v in found]
    assert len(ids) == len(set(ids)) <= ai.MAX_BY_VALUE + ai.MAX_BY_PROJECTION * len(SKILL) == 24
    assert all(v.position in SKILL for v in found) and not set(ids) & rs.rostered(state)
    assert ids[0] == fakes.BREAKOUT_ID                       # by week 6 he is the best player nobody has
    for position in SKILL:
        hottest = max((v for v in free if v.position == position), key=lambda v: v.projection)
        assert hottest.player_id in ids


def test_droppable_is_the_four_least_valuable_skill_players_and_never_a_top_hundred_player():
    state = league(3)
    protected = top_hundred(know(3))

    for team_id in CPUS:
        roster = values(state, team_id, 3)
        found = ai.droppable(roster, know(3), frozenset(protected))

        assert len(found) <= ai.DROPPABLE_COUNT
        assert all(v.position in SKILL and v.player_id not in protected for v in found)
        assert [v.healthy_value for v in found] == sorted(v.healthy_value for v in found)
        kept = [v for v in roster if v.position in SKILL and v.player_id not in protected and v not in found]
        assert all(v.healthy_value >= found[-1].healthy_value for v in kept)


@pytest.mark.parametrize("week", fakes.INJURED_WEEKS)
def test_an_injured_star_is_not_droppable_even_before_the_top_hundred_rule(week):
    star = know(week).by_id[fakes.INJURED_STAR_ID]

    assert star.out and star.last_projection > know(week).replacement["RB"]
    assert ai.droppable([star], know(week), frozenset()) == []
    assert fakes.INJURED_STAR_ID in top_hundred(know(week))            # and the second rule covers him too


def test_a_healthy_player_and_one_who_never_had_a_projection_can_be_dropped():
    healthy = know(3).by_id[fakes.INJURED_STAR_ID]
    ghost = know(7).by_id[fakes.NEVER_PLAYED_ID]

    assert ai.droppable([healthy], know(3), frozenset()) == [healthy]
    assert ghost.out and ai.droppable([ghost], know(7), frozenset()) == [ghost]


def test_kickers_and_defenses_are_never_droppable_for_a_claim():
    roster = values(league(), CPUS[0], 1)

    found = ai.droppable(roster, know(), frozenset())

    assert {v.position for v in roster} >= {"K", "DST"} and all(v.position in SKILL for v in found)


def test_a_kicker_who_is_out_is_not_droppable_and_does_not_break_the_search():
    kicker = fakes.player_id("K", fakes.KICKER_CHANGES[0][0])         # loses his job: no projection from then on
    lost = know(fakes.KICKER_CHANGES[0][2]).by_id[kicker]

    assert lost.out and ai.droppable([lost], know(fakes.KICKER_CHANGES[0][2]), frozenset()) == []


# ---------------- claims ----------------

def test_cpu_claims_are_well_formed_at_most_two_per_cpu_and_never_for_the_user():
    state = league(6)

    claims = ai.cpu_claims(state, SETTINGS, know(6), SEED)

    filed = [claim for team_claims in claims.values() for claim in team_claims]
    assert filed and set(claims) <= set(CPUS)
    assert sorted(c.id for c in filed) == list(range(state.next_id, state.next_id + len(filed)))
    for team_id, team_claims in claims.items():
        roster = rs.team(state, team_id).roster
        assert 1 <= len(team_claims) <= ai.MAX_CLAIMS
        assert all(c.team_id == team_id and c.add not in rs.rostered(state) and c.drop in roster for c in team_claims)
        assert len({c.add for c in team_claims}) == len({c.drop for c in team_claims}) == len(team_claims)


def test_every_first_claim_is_worth_a_lineup_point_a_week_after_its_drop():
    state, knowledge = league(6), know(6)
    horizon, projector = rv.horizon(knowledge, SETTINGS), rv.projector(knowledge)
    needed = ai.CLAIM_GAIN_PER_WEEK * sum(horizon.weights.values())

    claims = ai.cpu_claims(state, SETTINGS, knowledge, SEED)

    for team_id, (first, *_) in claims.items():
        gain = lineup_gain.gain(values(state, team_id, 6), horizon, projector, knowledge.league,
                                add=[knowledge.by_id[first.add]], drop=[knowledge.by_id[first.drop]])
        assert gain > needed


def test_the_breakout_back_is_not_claimed_in_week_one_although_the_season_data_knows_his_future():
    state = league(1)
    assert fakes.fake_season().projection(fakes.BREAKOUT_ID, 8) > 3 * know(1).by_id[fakes.BREAKOUT_ID].projection

    claims = ai.cpu_claims(state, SETTINGS, know(1), SEED)

    assert fakes.BREAKOUT_ID not in {c.add for team_claims in claims.values() for c in team_claims}


def test_cpus_claim_the_breakout_back_once_his_projections_rise_and_priority_decides_who_gets_him(season):
    wanted = [w for w in season if any(c.add == fakes.BREAKOUT_ID for cs in w.claims.values() for c in cs)]
    first = wanted[0]
    bidders = [team_id for team_id, cs in first.claims.items() if cs[0].add == fakes.BREAKOUT_ID]

    winner = next(t.id for t in first.after_claims.teams if fakes.BREAKOUT_ID in t.roster)

    assert 1 < first.week <= 6 and len(wanted) == 1          # not before the rise, and only until he is taken
    assert len(bidders) >= 2                                 # several teams put him first
    assert winner == next(t for t in first.before.waiver_order if t in bidders)
    assert all(fakes.BREAKOUT_ID in rs.team(w.after, winner).roster for w in season if w.week >= first.week)


def test_a_second_claim_is_planned_on_the_roster_left_by_the_first():
    state, knowledge = league(6), know(6)
    ctx = ai._context(knowledge, SETTINGS)
    pool = ai.candidates(knowledge, replay_waivers.available_players(state, knowledge))
    roster = values(state, CPUS[-1], 6)

    (first_add, first_drop, _), *rest = ai._plan_claims(roster, pool, ctx)

    after_first = [v for v in roster if v.player_id != first_drop] + [knowledge.by_id[first_add]]
    assert rest == ai._plan_claims(after_first, [v for v in pool if v.player_id != first_add], ctx)[:1]


@pytest.mark.parametrize("week", [1, 6, 11])
def test_the_cheap_ceiling_is_never_below_the_real_gain_of_adding_a_candidate(week):
    state, knowledge = league(week), know(week)
    ctx = ai._context(knowledge, SETTINGS)
    pool = ai.candidates(knowledge, replay_waivers.available_players(state, knowledge))

    for team_id in CPUS:
        roster = values(state, team_id, week)
        base, bars = ai._bars(roster, ctx)
        assert base == pytest.approx(lineup_gain.season_points(roster, ctx.horizon, ctx.projector, knowledge.league))
        for add in pool:
            real = lineup_gain.gain(roster, ctx.horizon, ctx.projector, knowledge.league, add=[add])
            assert ai._ceiling(add, bars, ctx) >= real - 0.011


# ---------------- free agency ----------------

def test_a_cpu_fills_an_empty_roster_spot_and_the_user_is_left_alone():
    spare_cpu, spare_user = at(league(), CPUS[0], "WR")[-1], at(league(), USER, "WR")[-1]
    state = without(without(league(), CPUS[0], spare_cpu), USER, spare_user)

    after = ai.cpu_free_agency(state, SETTINGS, know(), SEED)

    assert len(rs.team(after, CPUS[0]).roster) == SETTINGS.roster_size
    assert rs.team(after, USER).roster == rs.team(state, USER).roster
    assert all(e.team_id != USER for e in after.events)


def test_a_cpu_missing_a_kicker_adds_one_first_and_never_a_player_who_is_locked():
    kicker = at(league(), CPUS[0], "K")[0]
    state = without(league(), CPUS[0], kicker)
    best = max(replay_waivers.available_players(state, know(), "K"), key=lambda v: (v.projection, v.ros_ppg))
    locked = replace(state, locked=(best.player_id,))

    free_pick = at(ai.cpu_free_agency(state, SETTINGS, know(), SEED), CPUS[0], "K")
    locked_pick = at(ai.cpu_free_agency(locked, SETTINGS, know(), SEED), CPUS[0], "K")

    assert free_pick == [best.player_id]
    assert len(locked_pick) == 1 and locked_pick != free_pick
    assert best.player_id not in rs.rostered(ai.cpu_free_agency(locked, SETTINGS, know(), SEED))


def test_a_cpu_makes_at_most_two_free_agent_moves_a_week():
    gone = at(league(), CPUS[0], "WR")[-2:] + at(league(), CPUS[0], "RB")[-1:]
    state = without(league(), CPUS[0], *gone)

    after = ai.cpu_free_agency(state, SETTINGS, know(), SEED)

    assert len(rs.team(after, CPUS[0]).roster) == SETTINGS.roster_size - 3 + ai.MAX_FREE_AGENT_MOVES


def test_a_cpu_replaces_a_kicker_or_defense_on_bye_with_one_who_plays():
    found = 0
    for week in fakes.BYE_WEEKS:
        state, knowledge = league(week), know(week)
        resting = {(team_id, pid) for team_id in CPUS for position in ai.STREAMED
                   for pid in at(state, team_id, position, week) if knowledge.by_id[pid].projection == 0}

        after = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)

        for team_id, pid in resting:
            position = knowledge.by_id[pid].position
            if sum(e.team_id == team_id and e.kind == "add" for e in after.events) < ai.MAX_FREE_AGENT_MOVES:
                (new,) = at(after, team_id, position, week)
                assert new != pid and knowledge.by_id[new].projection > 0 and pid in after.locked
                found += 1
    assert found >= 8


def test_a_cpu_covers_a_starting_slot_that_would_be_empty_this_week():
    week, team_id = 7, CPUS[3]
    quarterbacks = at(league(week), team_id, "QB", week)
    data = fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in quarterbacks})
    knowledge = fakes.fake_knowledge(week, SETTINGS, data)
    state = league(week)

    after = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)

    roster = rs.team(after, team_id).roster
    starter = rv.best_lineup(knowledge, roster, SETTINGS)["QB"][0]
    assert starter not in quarterbacks and knowledge.by_id[starter].projection > 0
    assert len(roster) == SETTINGS.roster_size
    assert rv.can_field_lineup([knowledge.by_id[pid].position for pid in roster], SETTINGS)


@pytest.mark.parametrize("phase", ["waivers", "open"])       # a league with a weekly waiver run, and without
def test_a_cpu_covers_every_slot_nobody_plays_in_however_many_there_are(phase):
    week, team_id = 7, CPUS[3]
    resting = [pid for position in ("QB", "TE", "K", "DST") for pid in at(league(week), team_id, position, week)]
    data = fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in resting})
    knowledge = fakes.fake_knowledge(week, SETTINGS, data)
    state = league(week, phase)

    after = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)

    idle = rv.idle_slots(knowledge, rs.team(state, team_id).roster, SETTINGS)
    roster = rs.team(after, team_id).roster
    assert set(idle) >= {"QB", "TE", "K", "DST"} and len(idle) > ai.MAX_FREE_AGENT_MOVES
    assert rv.idle_slots(knowledge, roster, SETTINGS) == []
    assert sum(e.kind == "add" and e.team_id == team_id for e in after.events) == len(idle)
    assert len(roster) == SETTINGS.roster_size
    assert rv.can_field_lineup([knowledge.by_id[pid].position for pid in roster], SETTINGS)


def test_a_cpu_does_not_cover_one_slot_by_emptying_another():
    week, team_id = 3, CPUS[0]
    state = league(week)
    lineup = rv.best_lineup(know(week), rs.team(state, team_id).roster, SETTINGS)
    starting, tight_end = {pid for ids in lineup.values() for pid in ids}, lineup["TE"][0]
    bench = [v.player_id for v in values(state, team_id, week) if v.player_id not in starting and v.position in SKILL]
    data = fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in (tight_end, *bench)})
    knowledge = fakes.fake_knowledge(week, SETTINGS, data)
    roster = [knowledge.by_id[pid] for pid in rs.team(state, team_id).roster]
    free, ctx = replay_waivers.available_players(state, knowledge), ai._context(knowledge, SETTINGS)

    only_starters = ai._cover(roster, free, replace(ctx, protected=frozenset({tight_end, *bench})))
    anyone = ai._cover(roster, free, replace(ctx, protected=frozenset()))

    assert rv.idle_slots(knowledge, [v.player_id for v in roster], SETTINGS) == ["TE"]
    assert only_starters is None                             # whoever went would leave his own slot empty
    assert anyone is not None and knowledge.by_id[anyone[0]].position == "TE" and anyone[1] in bench


def test_a_defense_that_plays_is_swapped_only_for_a_clear_edge():
    state, knowledge = league(3), know(3)
    ctx, roster = ai._context(knowledge, SETTINGS), values(state, CPUS[0], 3)
    own = next(v for v in roster if v.position == "DST")
    other = replay_waivers.available_players(state, knowledge, "DST")[0]

    close = ai._stream(roster, [replace(other, projection=own.projection + ai.STREAM_EDGE - 0.1)], ctx)
    clear = ai._stream(roster, [replace(other, projection=own.projection + ai.STREAM_EDGE)], ctx)

    assert own.projection > 0 and ai.STREAM_EDGE >= 2.0      # never for less than two projected points
    assert close is None and clear == (other.player_id, own.player_id)


def test_free_agency_works_in_either_phase_and_gives_the_same_moves_every_time():
    state, knowledge = league(9, "open"), know(9)

    first = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)
    again = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)
    waivers = ai.cpu_free_agency(replace(state, phase="waivers"), SETTINGS, knowledge, SEED)

    assert first.events and first.teams == again.teams and first.events == again.events
    assert waivers.events and waivers.phase == "waivers"     # a dropped K / DST is claimable later there
    assert first.waiver_order == state.waiver_order and first.phase == "open"


# ---------------- a simulated stretch of the season ----------------

def test_no_roster_ever_exceeds_its_size_or_loses_the_ability_to_field_a_lineup(season):
    for week in season:
        for state in (week.after_claims, week.after):
            for team in state.teams:
                assert len(team.roster) <= SETTINGS.roster_size
                assert rv.can_field_lineup([week.know.by_id[pid].position for pid in team.roster], SETTINGS)
            assert sum(len(rs.held(t)) for t in state.teams) == len(rs.rostered(state))   # nobody on two teams


def test_cpus_never_drop_a_top_hundred_player_or_the_injured_star_and_never_touch_the_user(season):
    owner = next(t.id for t in season[0].before.teams if fakes.INJURED_STAR_ID in t.roster)
    for week in season:
        dropped = {e.player_ids[0] for e in week.events if e.kind == "drop"}
        dropped |= {e.player_ids[1] for e in week.events if e.kind in ("add", "claim") and len(e.player_ids) == 2}
        skill = {pid for pid in dropped if week.know.by_id[pid].position in SKILL}

        assert not skill & top_hundred(week.know)
        assert fakes.INJURED_STAR_ID in rs.held(rs.team(week.after, owner))
        assert (fakes.INJURED_STAR_ID in rs.team(week.after, owner).ir) == (week.week in fakes.INJURED_WEEKS)
        assert rs.held(rs.team(week.after, USER)) == rs.team(season[0].before, USER).roster
    assert owner != USER and {w.week for w in season} >= set(fakes.INJURED_WEEKS)


def test_cpus_stay_within_two_claims_and_two_free_agent_moves_beyond_the_slots_they_cover(season):
    for week in season:
        for team_id in CPUS:
            idle = len(rv.idle_slots(week.know, rs.team(week.after_claims, team_id).roster, SETTINGS))
            moves = sum(e.kind == "add" and e.team_id == team_id for e in week.events)

            assert len(week.claims.get(team_id, ())) <= ai.MAX_CLAIMS
            assert moves <= ai.MAX_FREE_AGENT_MOVES + idle           # one per slot covered: no back and forth
            assert len(rv.idle_slots(week.know, rs.team(week.after, team_id).roster, SETTINGS)) <= idle


def test_no_cpu_adds_to_a_position_it_is_full_at_while_one_of_its_own_plays_there(season):
    for week in season:
        for team_id in CPUS:
            before = [week.know.by_id[pid] for pid in rs.team(week.before, team_id).roster]
            after = [week.know.by_id[pid] for pid in rs.team(week.after, team_id).roster]
            for position, cap in (("QB", 2), ("TE", 2), ("K", 1), ("DST", 1)):
                had = [v for v in before if v.position == position]
                grew = sum(v.position == position for v in after) > max(cap, len(had))
                assert not (grew and any(v.projection > 0 for v in had)), (week.week, team_id, position)


def test_kickers_and_defenses_are_only_swapped_like_for_like_and_for_a_clear_edge(season):
    swaps = 0
    streaming = ai.streamers(SEED, sorted(CPUS))
    for week in season:
        for event in (e for e in week.events if e.kind in ("add", "claim") and len(e.player_ids) == 2):
            new, old = (week.know.by_id[pid] for pid in event.player_ids)
            assert (new.position in ai.STREAMED) == (old.position in ai.STREAMED)
            if new.position in ai.STREAMED:
                assert new.position == old.position and event.kind == "add"
                edge = ai.STREAMER_EDGE if event.team_id in streaming else ai.STREAM_EDGE
                assert old.projection == 0 or new.projection - old.projection >= edge
                swaps += 1
    assert swaps > 0


def test_waiver_activity_is_modest(season, record_property):
    won = [sum(e.kind == "claim" for e in week.events) for week in season]
    filed = [sum(len(cs) for cs in week.claims.values()) for week in season]
    moves = [sum(e.kind == "add" for e in week.events) for week in season]
    record_property("claims_won_per_week", sum(won) / len(season))
    record_property("claims_filed_per_week", sum(filed) / len(season))
    record_property("free_agent_moves_per_week", sum(moves) / len(season))

    assert 0.5 <= sum(won) / len(season) <= 10
    assert sum(filed) / len(season) <= len(CPUS) / 2
    assert max(filed) <= ai.MAX_CLAIMS * len(CPUS) * 3 / 4   # a breakout draws a claim from most CPUs; one wins
    assert 1 <= (sum(won) + sum(moves)) / len(season) <= 12


def test_claims_and_free_agency_for_eleven_cpus_fit_the_half_second_budget(own_cpu):
    state, knowledge = league(6), know(6)

    def fastest(step) -> float:
        times = []
        for _ in range(3):
            rv.clear_cache()                                 # cold projector: the worst case in a request
            fresh = know(6)
            started = time.perf_counter()
            step(state, SETTINGS, fresh, SEED)
            times.append(time.perf_counter() - started)
        return min(times)

    assert len(CPUS) == 11 and knowledge.week == 6
    assert fastest(ai.cpu_claims) < BUDGET_SECONDS
    assert fastest(lambda *args: ai.cpu_free_agency(ai.cpu_ir(*args), *args[1:])) < BUDGET_SECONDS


# ---------------- the IR slot ----------------

STAR, STAR_OWNER = fakes.INJURED_STAR_ID, 5


def tagged_know(week: int, tags: dict) -> Knowledge:
    return fakes.fake_knowledge(week, SETTINGS, fakes.tagged(fakes.fake_season(), tags))


def edited(state: State, team_id: int, roster: tuple[int, ...] | None = None, ir: tuple[int, ...] | None = None) -> State:
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=team.roster if roster is None else roster,
                                       ir=team.ir if ir is None else ir))


def ir_moves(before: State, after: State) -> list[tuple[int, tuple[int, ...]]]:
    return [(e.team_id, e.player_ids) for e in after.events[len(before.events):] if e.kind == "ir"]


def star_on_ir(week: int) -> State:
    """CPU 5's star put on IR by cpu_ir in week 6, the league then moved on to `week` (an open roster spot)."""
    return replace(ai.cpu_ir(league(6), SETTINGS, know(6), SEED), week=week)


def best_keeper(state: State, team_id: int, week: int, *besides: int) -> int:
    """The team's most valuable skill player above replacement (the kind a CPU stashes when he is hurt)."""
    keep = [v for v in values(state, team_id, week) if v.position in SKILL and v.player_id not in besides
            and v.last_projection > know(week).replacement[v.position]]
    return max(keep, key=lambda v: v.healthy_value).player_id


def test_a_cpu_puts_its_injured_star_on_ir_and_a_second_call_changes_nothing():     # IR-19, IR-21
    state = league(6)

    after = ai.cpu_ir(state, SETTINGS, know(6), SEED)

    assert rs.team(after, STAR_OWNER).ir == (STAR,) and STAR not in rs.team(after, STAR_OWNER).roster
    assert ir_moves(state, after) == [(STAR_OWNER, (STAR,))]
    assert [t for t in after.teams if t.id != STAR_OWNER] == [t for t in state.teams if t.id != STAR_OWNER]
    assert ai.cpu_ir(after, SETTINGS, know(6), SEED) == after
    assert ai.cpu_ir(league(5), SETTINGS, know(5), SEED) == league(5)           # no tag yet: nothing


def test_a_cpu_never_stashes_a_kicker_a_suspended_player_or_one_without_a_tag():
    week, team_id = fakes.IR_KICKER_WEEK, 4
    own_kicker = at(league(week), team_id, "K", week)[0]
    keeper = best_keeper(league(week), team_id, week)
    roster = tuple(fakes.IR_KICKER_ID if pid == own_kicker else pid for pid in rs.team(league(week), team_id).roster)
    state = edited(league(week), team_id, roster)
    owner = next(t.id for t in state.teams if fakes.NEVER_PLAYED_ID in t.roster)
    suspended = tagged_know(week, {(keeper, week): fakes.Injury("SUS")})
    hurt = tagged_know(week, {(keeper, week): fakes.Injury("O")})

    after = ai.cpu_ir(state, SETTINGS, suspended, SEED)

    assert suspended.by_id[fakes.IR_KICKER_ID].injury.tag == "IR" and suspended.by_id[keeper].out
    assert rs.team(after, team_id).ir == () and rs.team(after, owner).ir == () and owner != STAR_OWNER
    assert rs.team(ai.cpu_ir(state, SETTINGS, hurt, SEED), team_id).ir == (keeper,)      # an O tag: yes


def test_a_cpu_activates_a_player_who_lost_his_tag_into_its_open_spot_and_starts_him():     # IR-18 a
    state = star_on_ir(fakes.STAR_BACK_WEEK)

    after = ai.cpu_ir(state, SETTINGS, know(fakes.STAR_BACK_WEEK), SEED)

    team = rs.team(after, STAR_OWNER)
    assert len(rs.team(state, STAR_OWNER).roster) == SETTINGS.roster_size - 1
    assert team.ir == () and STAR in team.roster and ir_moves(state, after) == [(STAR_OWNER, (STAR,))]
    assert STAR in rv.best_lineup(know(fakes.STAR_BACK_WEEK), team.roster, SETTINGS)["RB"]


def test_activating_then_placing_is_one_of_each_and_never_a_still_eligible_player():     # IR-20
    week = fakes.STAR_BACK_WEEK
    state = star_on_ir(week)
    other = best_keeper(state, STAR_OWNER, week)
    hurt = tagged_know(week, {(other, week): fakes.Injury("IR")})

    after = ai.cpu_ir(state, SETTINGS, hurt, SEED)
    still_hurt = tagged_know(week, {(STAR, week): fakes.Injury("O"), (other, week): fakes.Injury("IR")})

    assert ir_moves(state, after) == [(STAR_OWNER, (STAR,)), (STAR_OWNER, (other,))]
    assert rs.team(after, STAR_OWNER).ir == (other,) and ai.cpu_ir(after, SETTINGS, hurt, SEED) == after
    assert ai.cpu_ir(state, SETTINGS, still_hurt, SEED) == state     # his slot is taken by a still-eligible star


def test_a_full_cpu_swaps_an_eligible_player_onto_ir_rather_than_cut_anyone():     # IR-18 b
    week = fakes.STAR_BACK_WEEK
    state = star_on_ir(week)
    free = replay_waivers.available_players(state, know(week), "WR")[-1].player_id
    state = edited(state, STAR_OWNER, (*rs.team(state, STAR_OWNER).roster, free))
    other = best_keeper(state, STAR_OWNER, week)
    hurt = tagged_know(week, {(other, week): fakes.Injury("PUP")})

    after = ai.cpu_ir(state, SETTINGS, hurt, SEED)

    team = rs.team(after, STAR_OWNER)
    assert ir_moves(state, after) == [(STAR_OWNER, (STAR, other))] and team.ir == (other,)
    assert STAR in team.roster and len(team.roster) == SETTINGS.roster_size and after.locked == state.locked


def test_a_full_cpu_with_nobody_to_swap_cuts_the_least_valuable_player_it_can_spare():     # IR-18 c
    week = fakes.STAR_BACK_WEEK
    state = star_on_ir(week)
    free = replay_waivers.available_players(state, know(week), "WR")[-1].player_id
    state = edited(state, STAR_OWNER, (*rs.team(state, STAR_OWNER).roster, free))
    ctx = ai._context(know(week), SETTINGS)
    expected = ai._spare(values(state, STAR_OWNER, week) + [know(week).by_id[STAR]], know(week), ctx.protected)[0]

    after = ai.cpu_ir(state, SETTINGS, know(week), SEED)

    team = rs.team(after, STAR_OWNER)
    assert STAR in team.roster and team.ir == () and len(team.roster) == SETTINGS.roster_size
    assert ir_moves(state, after) == [(STAR_OWNER, (STAR, expected.player_id))]
    assert expected.player_id in after.locked and expected.player_id != STAR


def test_a_cpu_cuts_the_ir_player_himself_when_he_is_the_one_it_can_best_spare():     # IR-18 c
    week = 3
    spare = next(v.player_id for v in ai._spare(values(league(week), STAR_OWNER, week), know(week), frozenset())
                 if v.position in ("RB", "WR"))             # (its least valuable are its only TE and QB)
    roster = tuple(pid for pid in rs.team(league(week), STAR_OWNER).roster if pid != spare)
    better = next(v.player_id for v in replay_waivers.available_players(league(week), know(week), "RB")
                  if v.healthy_value > know(week).by_id[spare].healthy_value)
    state = edited(league(week), STAR_OWNER, (*roster, better), (spare,))

    after = ai.cpu_ir(state, SETTINGS, know(week), SEED)

    assert spare not in rs.rostered(after) and spare in after.locked
    assert rs.team(after, STAR_OWNER).roster == (*roster, better) and rs.team(after, STAR_OWNER).ir == ()
    assert [(e.kind, e.player_ids) for e in after.events[len(state.events):]] == [("drop", (spare,))]


def test_a_cpu_that_can_spare_nobody_stays_blocked_and_free_agency_skips_it():     # IR-18 c, IR-21
    week = 3
    spare = ai._spare(values(league(week), STAR_OWNER, week), know(week), frozenset())[0].player_id
    roster = tuple(pid for pid in rs.team(league(week), STAR_OWNER).roster if pid != spare)
    blocked = edited(league(week), STAR_OWNER, roster, (spare,))         # an open spot it may not fill
    full = edited(blocked, STAR_OWNER, (*roster, fakes.IR_KICKER_ID))
    everyone = frozenset(v.player_id for v in know(week).values)
    ctx = replace(ai._context(know(week), SETTINGS), protected=everyone)

    stuck = ai._bring_back(full, ctx, rs.team(full, STAR_OWNER), spare)
    after = ai.cpu_free_agency(blocked, SETTINGS, know(week), SEED)

    assert stuck is full
    assert rs.team(after, STAR_OWNER) == rs.team(blocked, STAR_OWNER)
    assert any(e.team_id != STAR_OWNER for e in after.events)                  # the others still moved


def test_a_back_on_ir_still_counts_for_what_an_added_back_is_worth():     # 5.4 VALUE
    week = 6
    state = edited(league(week), STAR_OWNER, tuple(p for p in rs.team(league(week), STAR_OWNER).roster if p != STAR),
                   (STAR,))
    roster, ctx = values(state, STAR_OWNER, week), ai._context(know(week), SETTINGS)
    back = max(replay_waivers.available_players(state, know(week), "RB"), key=lambda v: v.ros_ppg)

    counted = ai._best_add(roster, [back], ai._for_team(ctx, rs.team(state, STAR_OWNER)), -1.0)
    ignored = ai._best_add(roster, [back], ctx, -1.0)

    assert ai._for_team(ctx, rs.team(state, STAR_OWNER)).stash == (know(week).by_id[STAR],) and ctx.stash == ()
    assert counted[0] < ignored[0]


def test_claims_and_free_agency_plan_each_team_with_its_own_ir_player(monkeypatch):
    state = ai.cpu_ir(league(6), SETTINGS, know(6), SEED)
    seen: dict[str, set] = {"claims": set(), "free agency": set()}
    plan, cover = ai._plan_claims, ai._cover
    monkeypatch.setattr(ai, "_plan_claims", lambda roster, pool, ctx: seen["claims"].add(
        (len(roster), tuple(v.player_id for v in ctx.stash))) or plan(roster, pool, ctx))
    monkeypatch.setattr(ai, "_cover", lambda roster, free, ctx: seen["free agency"].add(
        (len(roster), tuple(v.player_id for v in ctx.stash))) or cover(roster, free, ctx))

    ai.cpu_claims(state, SETTINGS, know(6), SEED)
    ai.cpu_free_agency(state, SETTINGS, know(6), SEED)

    for found in seen.values():                              # only CPU 5 is one short, with its star on IR
        assert (SETTINGS.roster_size - 1, (STAR,)) in found and (SETTINGS.roster_size, ()) in found
        assert (SETTINGS.roster_size - 1, ()) not in found


def test_covering_a_slot_with_a_full_roster_does_not_count_the_ir_player_against_the_limit():
    week, team_id = 3, 8
    quarterbacks = at(league(week), team_id, "QB", week)
    third = replay_waivers.available_players(league(week), know(week), "QB")[0].player_id
    data = fakes.tagged(fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in quarterbacks}),
                        {(third, week): fakes.Injury("O")})
    knowledge = fakes.fake_knowledge(week, SETTINGS, data)
    state = edited(league(week), team_id, ir=(third,))
    roster = [knowledge.by_id[pid] for pid in rs.team(state, team_id).roster]
    ctx = ai._for_team(ai._context(knowledge, SETTINGS), rs.team(state, team_id))
    free = replay_waivers.available_players(state, knowledge)

    move = ai._cover(roster, free, ctx)

    assert len(quarterbacks) == 2 and len(roster) == SETTINGS.roster_size and ctx.stash[0].player_id == third
    assert rv.idle_slots(knowledge, [v.player_id for v in roster], SETTINGS) == ["QB"]
    assert move is not None and knowledge.by_id[move[0]].position == "QB" and knowledge.by_id[move[0]].projection > 0
    assert move[1] in {v.player_id for v in ai._spare(roster, knowledge, ctx.protected)}


def test_a_cpu_with_its_ir_slot_taken_may_drop_a_long_term_injured_player_to_cover_a_slot():
    week, team_id = 3, 6
    state = league(week)
    tight_ends = at(state, team_id, "TE", week)
    stashed = replay_waivers.available_players(state, know(week), "RB")[0].player_id
    others = frozenset(v.player_id for v in values(state, team_id, week) if v.player_id not in tight_ends)

    def cover(tag: str = "IR", ir: tuple[int, ...] = (stashed,), protected: frozenset[int] = others):
        data = fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in tight_ends})
        data = fakes.tagged(data, {**{(pid, week): fakes.Injury(tag) for pid in tight_ends},
                                   (stashed, week): fakes.Injury("O")})
        knowledge = fakes.fake_knowledge(week, SETTINGS, data)
        team = rs.team(edited(state, team_id, ir=ir), team_id)
        ctx = replace(ai._context(knowledge, SETTINGS), protected=protected)
        roster = [knowledge.by_id[pid] for pid in team.roster]
        free = replay_waivers.available_players(edited(state, team_id, ir=ir), knowledge)
        return knowledge, roster, ctx, ai._cover(roster, free, ai._for_team(ctx, team))

    knowledge, roster, ctx, move = cover()

    hurt = sorted((knowledge.by_id[pid] for pid in tight_ends), key=lambda v: v.healthy_value)
    assert len(hurt) == 2 and all(v.last_projection > knowledge.replacement["TE"] for v in hurt)
    assert ai._spare(roster, knowledge, ctx.protected) == [] and len(roster) == SETTINGS.roster_size
    assert move is not None and move[1] == hurt[0].player_id
    assert knowledge.by_id[move[0]].position == "TE" and knowledge.by_id[move[0]].projection > 0
    assert cover(protected=others | set(tight_ends))[3] is None          # never a top player
    assert cover(tag="O")[3] is None                                    # an O tag may be back soon
    assert cover(ir=())[3] is None                                      # he could go on IR instead


def test_a_superflex_cpu_never_stashes_a_back_it_needs_to_fill_its_lineup():
    settings = fakes.fake_settings(starters=replay_slots.PRESETS["superflex"])
    week, team_id = 3, 6
    state = replace(fakes.drafted_state(settings), phase="waivers", week=week)
    plain = fakes.fake_knowledge(week, settings)
    roster = [plain.by_id[pid] for pid in rs.team(state, team_id).roster]
    backs = sorted((v for v in roster if v.position == "RB"), key=lambda v: -v.healthy_value)
    spare_qbs = iter(replay_waivers.available_players(state, plain, "QB"))
    qb_heavy = tuple(next(spare_qbs).player_id if v in backs[2:] else v.player_id for v in roster)
    state = edited(state, team_id, qb_heavy)                     # exactly the two backs the RB slots need
    star = backs[0]
    hurt = fakes.fake_knowledge(week, settings, fakes.tagged(fakes.fake_season(), {(star.player_id, week): fakes.Injury("O")}))
    ctx = ai._context(hurt, settings)

    after = ai.cpu_ir(state, settings, hurt, SEED)

    assert len(backs) > 2 and (star.player_id in ctx.protected or star.last_projection > hurt.replacement["RB"])
    assert rs.team(after, team_id).ir == () and star.player_id in rs.team(after, team_id).roster
    assert rv.can_field_lineup([hurt.by_id[pid].position for pid in rs.team(after, team_id).roster], settings)


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [1, 4, 6, 9])
def test_cpu_decisions_are_identical_when_everything_after_this_week_is_different(week):
    changed = fakes.fake_knowledge(week, SETTINGS, fakes.future_changed(fakes.fake_season(), after_week=week))
    state = ai.cpu_ir(league(week), SETTINGS, know(week), SEED)

    assert ai.cpu_ir(league(week), SETTINGS, changed, SEED) == state
    real_claims = ai.cpu_claims(state, SETTINGS, know(week), SEED)
    blind_claims = ai.cpu_claims(state, SETTINGS, changed, SEED)
    real = ai.cpu_free_agency(replay_waivers.process_claims(state, SETTINGS, know(week), real_claims),
                              SETTINGS, know(week), SEED)
    blind = ai.cpu_free_agency(replay_waivers.process_claims(state, SETTINGS, changed, blind_claims),
                               SETTINGS, changed, SEED)

    assert real_claims == blind_claims
    assert rs.to_json(real) == rs.to_json(blind)
    assert changed is not know(week)


@pytest.mark.parametrize("teams, protected", [(12, 100), (8, 66), (14, 116)])
def test_the_protected_top_scales_with_league_size(teams, protected):
    settings = fakes.fake_settings(teams=teams)

    ctx = ai._context(fakes.fake_knowledge(3, settings), settings)

    assert ai.protected_rank(settings) == protected and len(ctx.protected) == protected


def test_some_cpus_stream_and_the_pick_is_fixed_for_the_game():
    many = list(range(1, 201))

    picked = ai.streamers(SEED, many)

    assert picked == ai.streamers(SEED, many)                       # same game, same streamers every week
    assert 0.25 < len(picked) / len(many) < 0.55                    # about STREAMER_SHARE of them
    assert picked != ai.streamers(SEED + 1, many)


def test_a_streamer_swaps_a_defense_for_a_smaller_edge():
    state, knowledge = league(3), know(3)
    ctx, roster = ai._context(knowledge, SETTINGS), values(state, CPUS[0], 3)
    own = next(v for v in roster if v.position == "DST")
    other = replay_waivers.available_players(state, knowledge, "DST")[0]
    better = [replace(other, projection=own.projection + ai.STREAMER_EDGE)]

    assert ai._stream(roster, better, ctx) is None                                     # a holder keeps his
    assert ai._stream(roster, better, replace(ctx, stream_edge=ai.STREAMER_EDGE)) == (other.player_id, own.player_id)


@pytest.mark.parametrize("streams", [True, False])
def test_free_agency_gives_streamers_their_smaller_edge(monkeypatch, streams):
    """Every rostered defense projects 5, one free defense 7.5 (an edge between the two bars), the rest 0."""
    state, knowledge = league(3, "open"), know(3)
    rostered = rs.rostered(state)
    free = next(v.player_id for v in knowledge.values if v.position == "DST" and v.player_id not in rostered)
    assert ai.STREAMER_EDGE <= 2.5 < ai.STREAM_EDGE

    def projected(v: ReplayValue) -> ReplayValue:
        if v.position != "DST":
            return v
        return replace(v, projection=5.0 if v.player_id in rostered else 7.5 if v.player_id == free else 0.0)

    changed = tuple(projected(v) for v in knowledge.values)
    knowledge = replace(knowledge, values=changed, by_id=MappingProxyType({v.player_id: v for v in changed}))
    monkeypatch.setattr(ai, "streamers", lambda seed, cpu_ids: frozenset({CPUS[0]} if streams else ()))

    after = ai.cpu_free_agency(state, SETTINGS, knowledge, SEED)

    added = [e.team_id for e in after.events if e.kind == "add" and e.player_ids[0] == free]
    assert added == ([CPUS[0]] if streams else [])


# ---------------- stashing and trade-value adds ----------------

def test_a_cpu_stashes_its_most_valuable_eligible_player_even_a_bench_one():
    week, state = 6, league(6)
    team_id = next(t.id for t in state.teams if not t.is_user and not any(replay_ir.eligible(know(week), pid)
                                                                           for pid in t.roster))
    top = top_hundred(know(week))
    bench = sorted((v for v in values(state, team_id, week) if v.position in SKILL and v.chart > 0
                    and v.player_id not in top), key=lambda v: v.chart)
    low, higher = bench[0], bench[1]
    tags = {(low.player_id, week): fakes.Injury("O"), (higher.player_id, week): fakes.Injury("O")}
    # nobody is above replacement: the old rule stashed only top players or those above it
    hurt = replace(tagged_know(week, tags), replacement=MappingProxyType({p: 1e9 for p in SKILL}))

    after = ai.cpu_ir(state, SETTINGS, hurt, SEED)

    assert rs.team(after, team_id).ir == (higher.player_id,)     # a bench player, not a star, still frees his spot


def test_free_agency_adds_a_free_agent_worth_far_more_than_its_worst_player_and_stashes_him_when_out():
    week = 6
    state = league(week, phase="open")
    assert fakes.BREAKOUT_ID not in rs.rostered(state) and know(week).by_id[fakes.BREAKOUT_ID].chart > 300

    after = ai.cpu_free_agency(state, SETTINGS, know(week), SEED)
    hurt = tagged_know(week, {(fakes.BREAKOUT_ID, week): fakes.Injury("O")})
    stashed = ai.cpu_free_agency(state, SETTINGS, hurt, SEED)

    owner = next(t for t in after.teams if fakes.BREAKOUT_ID in t.roster)
    added = next(e for e in after.events if e.kind == "add" and e.player_ids[0] == fakes.BREAKOUT_ID)
    dropped = know(week).by_id[added.player_ids[1]]
    assert not owner.is_user and dropped.chart * ai.UPGRADE_SHARE <= know(week).by_id[fakes.BREAKOUT_ID].chart
    assert any(fakes.BREAKOUT_ID in t.ir for t in stashed.teams)


def test_free_agency_never_adds_by_trade_value_for_a_small_gap():
    week = 6
    state = league(week, phase="open")
    roster = values(state, 3, week)
    free = [v for v in ai._unrostered(state, know(week)) if v.position in SKILL]
    ctx = ai._for_team(ai._context(know(week), SETTINGS, state), rs.team(state, 3))
    worst = min(ai.droppable(roster, know(week), ctx.protected), key=lambda v: v.chart)
    close = [v for v in free if v.chart < worst.chart + ai.UPGRADE_GAP]

    assert ai._upgrade(roster, close, ctx) is None
