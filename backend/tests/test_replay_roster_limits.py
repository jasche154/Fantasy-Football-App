"""Replay League: the positional limits CPU managers keep when they add free agents, file claims and accept
trades (2 QB, 6 RB, 7 WR, 2 TE, 1 K, 1 DST), and the one exception: a week nobody they have there plays.

On fakes.drafted_state(): CPU 8 has exactly two quarterbacks, CPU 5 seven receivers (four behind its starters
and its FLEX), and the user owns two quarterbacks and five receivers.
"""
from collections import Counter
from dataclasses import replace

from services import replay_state as rs, replay_trades as rt, replay_values as rv, replay_waiver_ai as ai
from services.replay_pool import ALL_POSITIONS, SKILL
from services.replay_values import Knowledge, ReplayValue
from tests import replay_fakes as fakes

P = fakes.player_id
SETTINGS = fakes.fake_settings()
USER, FULL_AT_WR, TWO_QBS = 1, 5, 8
STAR_QB, OTHER_QB, USERS_SPARE_QB = P("QB", 1), P("QB", 2), P("QB", 18)
OVER = "more than they carry"


def league(week: int = 1) -> tuple[rs.State, Knowledge]:
    return replace(fakes.drafted_state(SETTINGS), week=week), fakes.fake_knowledge(week, SETTINGS)


def roster(state: rs.State, know: Knowledge, team_id: int) -> list[ReplayValue]:
    return [know.by_id[pid] for pid in rs.team(state, team_id).roster]


def held(state: rs.State, know: Knowledge, team_id: int) -> Counter:
    return Counter(v.position for v in roster(state, know, team_id))


def without_projection(week: int, player_ids: list[int]) -> Knowledge:
    """This week's knowledge if those players had no projection (bye or out)."""
    data = fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in player_ids})
    return fakes.fake_knowledge(week, SETTINGS, data)


# ---------------- the limits ----------------

def test_the_limits_are_the_ones_the_draft_keeps():
    two_quarterbacks = fakes.fake_settings(starters={**SETTINGS.starters, "QB": 2})

    caps = {position: rv.position_cap(position, SETTINGS) for position in ALL_POSITIONS}

    assert caps == {"QB": 2, "RB": 6, "WR": 7, "TE": 2, "K": 1, "DST": 1}
    assert rv.position_cap("QB", two_quarterbacks) == 3


def test_one_more_is_fine_only_when_the_ones_it_keeps_cannot_start_and_he_can():
    state, know = league(3)
    mine = [v for v in roster(state, know, TWO_QBS) if v.position == "QB"]
    new, another = know.by_id[STAR_QB], know.by_id[OTHER_QB]
    resting = [replace(v, projection=0.0) for v in mine]
    assert len(mine) == 2 and all(v.projection > 0 for v in (*mine, new, another))

    assert rv.crowded(mine, [new], "QB", SETTINGS)
    assert not rv.crowded(mine[:1], [new], "QB", SETTINGS)                       # room under the limit
    assert not rv.crowded(resting, [new], "QB", SETTINGS)                        # the only way to start one
    assert rv.crowded(resting, [replace(new, projection=0.0)], "QB", SETTINGS)   # he does not play either
    assert rv.crowded([resting[0], mine[1]], [new], "QB", SETTINGS)              # its other one plays
    assert rv.crowded(resting, [new, another], "QB", SETTINGS)                   # one slot, so one extra


# ---------------- free agents and claims ----------------

def test_a_cpu_at_its_limit_adds_there_only_by_swapping_like_for_like():
    state, know = league(3)
    ctx, mine = ai._context(know, SETTINGS), roster(state, know, TWO_QBS)
    anyone = [v for v in mine if v.position in SKILL]

    for_a_quarterback = ai._legal_drops(mine, know.by_id[STAR_QB], anyone, ctx)
    for_a_back = ai._legal_drops(mine, know.by_id[P("RB", 1)], anyone, ctx)

    assert [v.position for v in for_a_quarterback] == ["QB", "QB"]
    assert {v.position for v in for_a_back} == set(SKILL) and held(state, know, TWO_QBS)["RB"] < 6


def test_an_open_roster_spot_does_not_go_to_a_third_quarterback():
    state, know = league(3)
    ctx, mine = ai._context(know, SETTINGS), roster(state, know, TWO_QBS)
    backup = min((v for v in mine if v.position == "QB"), key=lambda v: v.ros_ppg)
    last_back = min((v for v in mine if v.position == "RB"), key=lambda v: v.ros_ppg)
    star, free = know.by_id[STAR_QB], [know.by_id[STAR_QB], last_back]
    two, one = [v for v in mine if v is not last_back], [v for v in mine if v is not last_back and v is not backup]

    assert ai._fill_spot(two, free, ctx) == (last_back.player_id, None)
    assert ai._fill_spot(two, [star], ctx) is None           # not even when he is the only one there
    assert ai._fill_spot(one, free, ctx) == (STAR_QB, None)  # he is who it wants when it has room for him
    assert ai._best_add(two, [star], ctx, 0.0) is None and ai._best_add(one, [star], ctx, 0.0)[1] == star


def test_a_hot_quarterback_is_claimed_only_by_swapping_one_where_the_limit_is_reached():
    week = 2
    state, plain = league(week)
    state = replace(state, phase="waivers")
    third = next(v.player_id for v in plain.values if v.position == "QB" and v.player_id not in rs.rostered(state))
    know = fakes.fake_knowledge(week, SETTINGS, fakes.patched(fakes.fake_season(), actual={(third, 1): 45.0}))
    ctx = ai._for_team(replace(ai._context(know, SETTINGS), hot=frozenset({third}), hot_gain=0.0),
                       rs.team(state, TWO_QBS))
    hot_qb, mine = know.by_id[third], roster(state, know, TWO_QBS)

    claims = ai.cpu_claims(state, SETTINGS, know, 0)

    assert third in {c.add for cs in claims.values() for c in cs}           # a hot add the CPUs want
    assert all(know.by_id[c.drop].position == "QB" for c in claims.get(TWO_QBS, ()) if c.add == third)
    assert {v.position for v in ai._legal_drops(mine, hot_qb, mine, ctx)} == {"QB"}


def test_an_extra_quarterback_is_added_the_week_neither_plays_and_let_go_the_week_after():
    week = 7
    state, _ = league(week)
    own = [v.player_id for v in roster(state, fakes.fake_knowledge(week), TWO_QBS) if v.position == "QB"]
    know = without_projection(week, own)

    after = ai.cpu_free_agency(state, SETTINGS, know, 0)
    later = ai.cpu_free_agency(replace(after, week=week + 1, locked=()), SETTINGS,
                               fakes.fake_knowledge(week + 1), 0)

    starter = rv.best_lineup(know, rs.team(after, TWO_QBS).roster, SETTINGS)["QB"][0]
    assert starter not in own and know.by_id[starter].projection > 0
    assert held(after, know, TWO_QBS)["QB"] == 3 and held(later, know, TWO_QBS)["QB"] == 2
    assert len(rs.team(later, TWO_QBS).roster) == SETTINGS.roster_size


def on_ir(state: rs.State, team_id: int, player_id: int) -> rs.State:
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p != player_id), ir=(player_id,)))


def test_a_receiver_on_ir_still_counts_toward_the_limit_while_others_play_there():
    week = 3
    state, _ = league(week)
    hurt = max((v for v in roster(state, fakes.fake_knowledge(week), FULL_AT_WR) if v.position == "WR"),
               key=lambda v: v.healthy_value).player_id
    know = fakes.fake_knowledge(week, SETTINGS, fakes.tagged(fakes.fake_season(), {(hurt, week): fakes.Injury("O")}))
    state = on_ir(state, FULL_AT_WR, hurt)
    ctx, mine = ai._context(know, SETTINGS), roster(state, know, FULL_AT_WR)
    receiver = max((v for v in know.values if v.position == "WR" and v.player_id not in rs.rostered(state)),
                   key=lambda v: v.ros_ppg)
    counted = ai._for_team(ctx, rs.team(state, FULL_AT_WR))

    assert held(state, know, FULL_AT_WR)["WR"] == 6 and len(mine) == SETTINGS.roster_size - 1
    assert ai._fill_spot(mine, [receiver], ctx) == (receiver.player_id, None)     # if he counted for nothing
    assert ai._fill_spot(mine, [receiver], counted) is None
    assert ai._best_add(mine, [receiver], counted, 0.0) is None
    anyone = [v for v in mine if v.position in SKILL]
    assert {v.position for v in ai._legal_drops(mine, receiver, anyone, ctx)} > {"WR"}
    assert {v.position for v in ai._legal_drops(mine, receiver, anyone, counted)} == {"WR"}   # like for like
    crowded = [*mine, receiver]                             # seven receivers playing, one more on IR
    free = [v for v in know.values if v.player_id not in rs.rostered(state) | {receiver.player_id}]
    assert ai._trim(crowded, free, ctx) is None
    trimmed = ai._trim(crowded, free, counted)
    assert trimmed is not None and know.by_id[trimmed[1]].position == "WR" != know.by_id[trimmed[0]].position


def test_a_quarterback_on_ir_does_not_stop_a_cpu_covering_the_week_its_other_two_do_not_play():
    week = 7
    state, _ = league(week)
    own = [v.player_id for v in roster(state, fakes.fake_knowledge(week), TWO_QBS) if v.position == "QB"]
    third = next(v.player_id for v in fakes.fake_knowledge(week).values
                 if v.position == "QB" and v.player_id not in rs.rostered(state))
    data = fakes.tagged(fakes.patched(fakes.fake_season(), projected={(pid, week): None for pid in own}),
                        {(third, week): fakes.Injury("IR")})
    know = fakes.fake_knowledge(week, SETTINGS, data)
    spare = min((v for v in roster(state, know, TWO_QBS) if v.position == "WR"), key=lambda v: v.healthy_value)
    team = rs.team(state, TWO_QBS)
    state = rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p != spare.player_id),
                                        ir=(third,)))
    ctx = ai._context(know, SETTINGS)
    free = [v for v in know.values if v.player_id not in rs.rostered(state)]

    move = ai._cover(roster(state, know, TWO_QBS), free, ai._for_team(ctx, rs.team(state, TWO_QBS)))

    assert rv.idle_slots(know, rs.team(state, TWO_QBS).roster, SETTINGS) == ["QB"]
    assert move is not None and know.by_id[move[0]].position == "QB" and move[1] is None


def test_a_spot_opened_by_an_ir_move_is_filled_within_the_limits():
    week = 3
    state, _ = league(week)
    hurt = max((v for v in roster(state, fakes.fake_knowledge(week), FULL_AT_WR) if v.position == "WR"),
               key=lambda v: v.healthy_value).player_id
    know = fakes.fake_knowledge(week, SETTINGS, fakes.tagged(fakes.fake_season(), {(hurt, week): fakes.Injury("IR")}))

    after = ai.cpu_free_agency(ai.cpu_ir(state, SETTINGS, know, 0), SETTINGS, know, 0)

    team = rs.team(after, FULL_AT_WR)
    owned = Counter(know.by_id[pid].position for pid in rs.held(team))
    assert team.ir == (hurt,) and len(team.roster) == SETTINGS.roster_size
    assert all(owned[position] <= rv.position_cap(position, SETTINGS) for position in ALL_POSITIONS), owned


# ---------------- trades ----------------

def test_a_trade_past_a_limit_cuts_one_there_and_names_no_limit_reason():
    """A CPU with two quarterbacks may take a third, and one with seven receivers an eighth, but the deal
    itself cuts its cheapest one there (counted in its tally like any cut of its own): no CPU carries more
    after a trade than its limit or than it already did."""
    state, know = league()
    theirs = roster(state, know, TWO_QBS)
    others = [v.player_id for v in theirs if v.position in ("RB", "WR")]

    added = [rt.evaluate(state, SETTINGS, know, USER, TWO_QBS, [USERS_SPARE_QB], [pid]) for pid in others]
    one_more = rt.evaluate(state, SETTINGS, know, USER, FULL_AT_WR, [P("WR", 16)], [P("RB", 27)])

    assert held(state, know, FULL_AT_WR)["WR"] == rv.position_cap("WR", SETTINGS) == 7
    assert not any(OVER in reason for verdict in (*added, one_more) for reason in verdict.reasons)
    assert all([know.by_id[pid].position for pid in verdict.their_drops] == ["QB"] for verdict in added)
    assert [know.by_id[pid].position for pid in one_more.their_drops] == ["WR"]
