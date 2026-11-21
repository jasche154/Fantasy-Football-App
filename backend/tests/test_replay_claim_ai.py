"""Replay League: the CPU's hot waiver route and FAAB bids (services/replay_claim_ai.py, used by
replay_waiver_ai.cpu_claims), on the fake league with scripted breakouts (a low-ADP receiver's big game)."""
from dataclasses import replace

import pytest

from services import replay_claim_ai as hot, replay_state as rs, replay_values as rv, replay_waiver_ai as ai
from services.replay_values import Knowledge
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings()
FAAB = fakes.fake_settings(waiver_mode="faab")
SEED = 7
BREAKOUT = fakes.player_id("WR", 80)        # undrafted, never claimed without a big game


def know(week: int, actual: dict | None = None, settings=SETTINGS) -> Knowledge:
    data = fakes.fake_season()
    return fakes.fake_knowledge(week, settings, fakes.patched(data, actual=actual) if actual else data)


def league(week: int, settings=SETTINGS) -> rs.State:
    return replace(fakes.drafted_state(settings), week=week, phase="waivers")


def adds(claims: dict) -> list[int]:
    return [c.add for team_claims in claims.values() for c in team_claims]


class Dice:
    """A stand-in for the seeded generator: uniform() always gives the same factor."""

    def __init__(self, factor: float = 1.0):
        self.factor = factor

    def uniform(self, low: float, high: float) -> float:
        assert (low, high) == (1 - hot.BID_NOISE, 1 + hot.BID_NOISE)
        return self.factor


# ---------------- the hot signal ----------------

def test_hot_rate_is_half_the_last_two_games_and_half_the_rest_of_season_rate():
    value = replace(know(4).by_id[BREAKOUT], recent_points=(20.0, 10.0, 99.0), ros_ppg=5.0)

    assert hot.hot_rate(value) == pytest.approx(0.5 * 15.0 + 0.5 * 5.0)


def test_hot_needs_a_six_point_gap_a_recent_game_a_skill_position_and_no_out():
    knowledge = know(4)
    base = replace(knowledge.by_id[BREAKOUT], recent_points=(11.0, 11.0), ros_ppg=5.0, last_game_week=3, out=False)
    kicker = next(v for v in knowledge.values if v.position == "K")

    assert hot.is_hot(knowledge, base)                                           # gap exactly 6.0
    assert not hot.is_hot(knowledge, replace(base, ros_ppg=5.1))                  # gap 5.9
    assert not hot.is_hot(knowledge, replace(base, out=True))
    assert hot.is_hot(knowledge, replace(base, last_game_week=2))                # two weeks ago
    assert not hot.is_hot(knowledge, replace(base, last_game_week=1))            # stale
    assert not hot.is_hot(knowledge, replace(base, recent_points=()))
    assert not hot.is_hot(knowledge, replace(kicker, recent_points=(30.0, 30.0), ros_ppg=5.0, last_game_week=3,
                                             out=False))


def test_hot_rates_cover_hot_players_on_rosters_and_off_them_and_are_cached_per_knowledge():
    state = league(2)
    owned = next(pid for pid in rs.team(state, 2).roster[::-1] if know(2).by_id[pid].position == "WR")
    knowledge = know(2, {(BREAKOUT, 1): 30.0, (owned, 1): 40.0})

    rates = hot.hot_rates(knowledge)

    assert BREAKOUT not in rs.rostered(state) and {BREAKOUT, owned} <= set(rates)
    assert rates[BREAKOUT] == pytest.approx(hot.hot_rate(knowledge.by_id[BREAKOUT]))
    assert hot.hot_rates(knowledge) is rates and BREAKOUT not in hot.hot_rates(know(2))


def test_the_hot_projector_never_touches_the_shared_projector_and_keeps_this_weeks_projection():
    knowledge = know(2, {(BREAKOUT, 1): 30.0})
    value, shared = knowledge.by_id[BREAKOUT], rv.projector(knowledge)
    plain = rv.projector.__wrapped__(knowledge)                  # a fresh plain one, outside the cache
    week = next(w for w in range(3, 10) if plain.points(value, w) > 0)

    projector = hot.HotProjector(knowledge, hot.hot_rates(knowledge))
    ai.cpu_claims(league(2), SETTINGS, knowledge, SEED)

    assert projector.points(value, week) > plain.points(value, week) + 3
    assert projector.points(value, 2) == plain.points(value, 2) == value.projection
    assert shared.points(value, week) == plain.points(value, week)
    assert rv.projector(knowledge) is shared


# ---------------- the candidate list ----------------

def test_a_hot_free_agent_joins_the_short_list_and_at_most_six_do_biggest_gap_first():
    knowledge = know(2, {(BREAKOUT, 1): 30.0})
    free = ai._unrostered(league(2), knowledge)
    listed = ai.candidates(knowledge, free)

    assert BREAKOUT not in {v.player_id for v in listed}
    assert hot.hot_candidates(knowledge, free, listed)[0].player_id == BREAKOUT
    quiet = ai._unrostered(league(2), know(2))
    assert BREAKOUT not in {v.player_id for v in hot.hot_candidates(know(2), quiet, ai.candidates(know(2), quiet))}

    receivers = [fakes.player_id("WR", rank) for rank in range(81, 91)]
    kicker = next(v.player_id for v in free if v.position == "K")
    many = know(2, {**{(pid, 1): 20.0 + n for n, pid in enumerate(receivers)}, (kicker, 1): 60.0})
    free = ai._unrostered(league(2), many)
    found = hot.hot_candidates(many, free, ai.candidates(many, free))

    assert len(found) == hot.MAX_HOT and kicker not in {v.player_id for v in found}
    gaps = [hot.hot_gap(v) for v in found]
    assert gaps == sorted(gaps, reverse=True)


# ---------------- claims ----------------

@pytest.mark.parametrize("settings", [SETTINGS, FAAB])
def test_a_breakout_is_claimed_at_the_first_run_after_his_big_game_and_not_without_it(settings):
    for week in (2, 6):                                          # after a week-1 game, and after a week-5 game
        big = know(week, {(BREAKOUT, week - 1): 30.0}, settings)

        claimed = ai.cpu_claims(league(week, settings), settings, big, SEED)
        plain = ai.cpu_claims(league(week, settings), settings, know(week, settings=settings), SEED)

        assert BREAKOUT in adds(claimed) and BREAKOUT not in adds(plain)


def test_a_hot_add_needs_only_the_lower_bar_and_others_the_usual_one():
    knowledge = know(2, {(BREAKOUT, 1): 30.0})
    state, team = league(2), rs.team(league(2), 12)
    roster = [knowledge.by_id[pid] for pid in team.roster]
    ctx = replace(ai._context(knowledge, SETTINGS), projector=hot.HotProjector(knowledge, hot.hot_rates(knowledge)))
    add = knowledge.by_id[BREAKOUT]
    gain = ai._best_add(roster, [add], ctx, -100.0)[0]

    as_hot = replace(ctx, hot=frozenset({BREAKOUT}), hot_gain=gain - 0.01)

    assert state.week == 2 and gain > 0
    assert ai._best_add(roster, [add], ctx, gain + 5) is None                      # the usual bar
    assert ai._best_add(roster, [add], as_hot, gain + 5)[1] is add                 # the hot bar
    assert ai._best_add(roster, [add], replace(as_hot, hot_gain=gain + 0.01), gain - 5) is None


def test_a_full_roster_compares_a_hot_add_with_its_drops_at_his_hot_rate():
    knowledge = know(2)
    roster = [knowledge.by_id[pid] for pid in rs.team(league(2), 12).roster]
    add = replace(knowledge.by_id[BREAKOUT], ros_ppg=0.5, projection=0.5)   # below everyone it could drop
    ctx = replace(ai._context(knowledge, SETTINGS), projector=hot.HotProjector(knowledge, {BREAKOUT: 25.0}))

    assert len(roster) == SETTINGS.roster_size
    assert ai._best_add(roster, [add], ctx, 0.0)[1] is add


def test_free_agency_ignores_hot_streaks():
    state = replace(fakes.drafted_state(SETTINGS), week=2, phase="open")
    team = rs.team(state, 12)
    state = rs.with_team(state, replace(team, roster=team.roster[:-1]))        # an open roster spot

    plain = ai.cpu_free_agency(state, SETTINGS, know(2), SEED)
    big = ai.cpu_free_agency(state, SETTINGS, know(2, {(BREAKOUT, 1): 30.0}), SEED)

    assert rs.to_json(plain) == rs.to_json(big)


# ---------------- FAAB bids ----------------

def test_a_bid_is_a_share_of_what_is_left_by_gain_and_pace():
    assert hot.cpu_bid(25.0, 10.0, 100, 16, Dice()) == 20                          # 0.08 x 2.5 a week
    assert hot.cpu_bid(25.0, 10.0, 100, 6, Dice()) == 26                           # pace 8/6
    assert hot.cpu_bid(25.0, 10.0, 50, 16, Dice(1.2)) == 12                        # noise, half the budget
    assert hot.cpu_bid(0.1, 10.0, 100, 16, Dice()) == 0


def test_a_bid_is_at_most_forty_percent_until_the_last_four_runs_and_never_above_the_budget():
    assert hot.cpu_bid(500.0, 10.0, 100, 5, Dice(1.2)) == 40
    assert hot.cpu_bid(500.0, 10.0, 100, 4, Dice(1.2)) == 100
    assert hot.cpu_bid(500.0, 10.0, 7, 1, Dice(1.2)) == 7
    assert hot.cpu_bid(500.0, 10.0, 0, 1, Dice(1.2)) == 0


def test_cpu_claims_bid_only_in_faab_leagues_deterministically_and_blind():
    big = {(BREAKOUT, 1): 30.0}
    state = replace(league(2, FAAB), faab_spent={team_id: 50 for team_id in range(1, 13)})

    faab = ai.cpu_claims(state, FAAB, know(2, big, FAAB), SEED)
    again = ai.cpu_claims(state, FAAB, know(2, big, FAAB), SEED)
    other_seed = ai.cpu_claims(state, FAAB, know(2, big, FAAB), SEED + 1)
    wednesday = ai.cpu_claims(league(2), SETTINGS, know(2, big), SEED)

    bids = [c.bid for cs in faab.values() for c in cs]
    assert faab == again and any(bids) and all(0 <= bid <= 50 for bid in bids)
    assert [c.bid for cs in other_seed.values() for c in cs] != bids
    assert adds(wednesday) == adds(faab) and all(c.bid == 0 for cs in wednesday.values() for c in cs)


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [2, 4, 6])
def test_claims_and_bids_are_identical_when_everything_after_this_week_is_different(week):
    data = fakes.patched(fakes.fake_season(), actual={(BREAKOUT, week - 1): 30.0})
    real = fakes.fake_knowledge(week, FAAB, data)
    blind = fakes.fake_knowledge(week, FAAB, fakes.future_changed(data, after_week=week))

    claims = ai.cpu_claims(league(week, FAAB), FAAB, real, SEED)

    assert blind is not real and BREAKOUT in adds(claims)
    assert ai.cpu_claims(league(week, FAAB), FAAB, blind, SEED) == claims
    assert hot.hot_rates(blind) == hot.hot_rates(real)
