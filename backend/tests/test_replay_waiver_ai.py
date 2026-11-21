"""Replay League CPU waiver logic (services/replay_waiver_ai.py), on the fake league.

`season` plays the CPU side of twelve weeks (claims -> waiver run -> free agency -> next week) once for the
module; the behaviour tests read that log, the unit tests use one week of a freshly drafted league.
"""
import time
from dataclasses import dataclass, replace

import pytest

from services import lineup_gain, replay_state as rs, replay_values as rv, replay_waiver_ai as ai, replay_waivers
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
    state, weeks = fakes.drafted_state(SETTINGS), []
    for week in range(1, SIM_WEEKS + 1):
        before = replace(state, week=week, phase="waivers", locked=())
        knowledge = know(week)
        claims = ai.cpu_claims(before, SETTINGS, knowledge, SEED)
        after_claims = replay_waivers.process_claims(before, SETTINGS, knowledge, claims)
        state = ai.cpu_free_agency(after_claims, SETTINGS, knowledge, SEED)
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

    (first_add, first_drop), *rest = ai._plan_claims(roster, pool, ctx)

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
    again = ai.cpu_free_agency(replace(state, phase="waivers"), SETTINGS, knowledge, SEED + 1)

    assert first.events and first.teams == again.teams and first.events == again.events
    assert first.waiver_order == state.waiver_order and first.phase == "open"


# ---------------- a simulated stretch of the season ----------------

def test_no_roster_ever_exceeds_its_size_or_loses_the_ability_to_field_a_lineup(season):
    for week in season:
        for state in (week.after_claims, week.after):
            for team in state.teams:
                assert len(team.roster) <= SETTINGS.roster_size
                assert rv.can_field_lineup([week.know.by_id[pid].position for pid in team.roster], SETTINGS)
            assert sum(len(t.roster) for t in state.teams) == len(rs.rostered(state))     # nobody on two teams


def test_cpus_never_drop_a_top_hundred_player_or_the_injured_star_and_never_touch_the_user(season):
    owner = next(t.id for t in season[0].before.teams if fakes.INJURED_STAR_ID in t.roster)
    for week in season:
        dropped = {e.player_ids[0] for e in week.events if e.kind == "drop"}
        dropped |= {e.player_ids[1] for e in week.events if e.kind in ("add", "claim") and len(e.player_ids) == 2}
        skill = {pid for pid in dropped if week.know.by_id[pid].position in SKILL}

        assert not skill & top_hundred(week.know)
        assert fakes.INJURED_STAR_ID in rs.team(week.after, owner).roster
        assert rs.team(week.after, USER).roster == rs.team(season[0].before, USER).roster
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
    for week in season:
        for event in (e for e in week.events if e.kind in ("add", "claim") and len(e.player_ids) == 2):
            new, old = (week.know.by_id[pid] for pid in event.player_ids)
            assert (new.position in ai.STREAMED) == (old.position in ai.STREAMED)
            if new.position in ai.STREAMED:
                assert new.position == old.position and event.kind == "add"
                assert old.projection == 0 or new.projection - old.projection >= ai.STREAM_EDGE
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
    assert max(filed) <= ai.MAX_CLAIMS * len(CPUS) / 2
    assert 1 <= (sum(won) + sum(moves)) / len(season) <= 12


def test_claims_and_free_agency_for_eleven_cpus_fit_the_half_second_budget():
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
    assert fastest(ai.cpu_free_agency) < BUDGET_SECONDS


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [1, 4, 6, 9])
def test_cpu_decisions_are_identical_when_everything_after_this_week_is_different(week):
    state = league(week)
    changed = fakes.fake_knowledge(week, SETTINGS, fakes.future_changed(fakes.fake_season(), after_week=week))

    real_claims = ai.cpu_claims(state, SETTINGS, know(week), SEED)
    blind_claims = ai.cpu_claims(state, SETTINGS, changed, SEED)
    real = ai.cpu_free_agency(replay_waivers.process_claims(state, SETTINGS, know(week), real_claims),
                              SETTINGS, know(week), SEED)
    blind = ai.cpu_free_agency(replay_waivers.process_claims(state, SETTINGS, changed, blind_claims),
                               SETTINGS, changed, SEED)

    assert real_claims == blind_claims
    assert rs.to_json(real) == rs.to_json(blind)
    assert changed is not know(week)
