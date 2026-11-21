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


# ---------------- trades ----------------

def test_a_cpu_with_two_quarterbacks_will_not_trade_for_a_third_but_swaps_one():
    state, know = league()
    theirs = roster(state, know, TWO_QBS)
    others = [v.player_id for v in theirs if v.position in ("RB", "WR")]
    backup = min((v for v in theirs if v.position == "QB"), key=lambda v: v.ros_ppg).player_id

    added = [rt.evaluate(state, SETTINGS, know, USER, TWO_QBS, [USERS_SPARE_QB], [pid]) for pid in others]
    swapped = rt.evaluate(state, SETTINGS, know, USER, TWO_QBS, [USERS_SPARE_QB], [backup])

    assert all(f"It would leave CPU 8 with 3 at QB, {OVER}." in verdict.reasons for verdict in added)
    assert not any(verdict.accepted for verdict in added) and OVER not in " ".join(swapped.reasons)


def test_the_week_its_own_quarterbacks_do_not_play_a_cpu_takes_one_who_does():
    state, _ = league()
    own = [v.player_id for v in roster(state, fakes.fake_knowledge(1), TWO_QBS) if v.position == "QB"]
    know = without_projection(1, own)
    scrub = min((v for v in roster(state, know, TWO_QBS) if v.position == "WR"), key=lambda v: v.market_value)

    verdict = rt.evaluate(state, SETTINGS, know, USER, TWO_QBS, [USERS_SPARE_QB], [scrub.player_id])

    assert know.by_id[USERS_SPARE_QB].projection > 0 and OVER not in " ".join(verdict.reasons)


def test_a_cpu_with_a_bench_full_of_receivers_will_not_trade_for_another():
    """The owner's rule: "they won't trade for a WR if they have 6 on the bench". CPU 5 would start the
    receiver it is offered, so the lineup test alone lets the deal through."""
    state, know = league()
    receiver, back, their_receiver = P("WR", 16), P("RB", 27), P("WR", 26)

    one_more = rt.evaluate(state, SETTINGS, know, USER, FULL_AT_WR, [receiver], [back])
    swap = rt.evaluate(state, SETTINGS, know, USER, FULL_AT_WR, [receiver], [their_receiver])

    assert held(state, know, FULL_AT_WR)["WR"] == rv.position_cap("WR", SETTINGS) == 7
    assert one_more.their_gain >= rt.MIN_TRADE_GAIN and one_more.value_give >= one_more.value_get
    assert one_more.reasons == (f"It would leave CPU 5 with 8 at WR, {OVER}.",)
    assert swap.accepted                                     # a better receiver for one of its own is fine
