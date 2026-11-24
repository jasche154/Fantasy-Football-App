"""sim_world / sim_teams: the public belief, read point-in-time from a SeasonData."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import sim_world
from services.replay_pool import Injury
from tests import replay_fakes as fakes

WEEK = 8


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


@pytest.fixture(scope="module")
def belief(data):
    return sim_world.estimate(data, WEEK)


def test_the_belief_covers_every_team_and_every_known_player_on_one(data, belief):
    assert set(belief.teams) == {team for team, _ in data.schedule.opponents}
    known = data.as_of(WEEK)
    on_team = {pid for pid in known.players if known.team_at(pid, WEEK)}
    assert set(belief.players) == on_team
    assert all(p.team == known.team_at(p.player_id, WEEK) for p in belief.players.values())


def test_bigger_roles_get_bigger_weights(data, belief):
    team = belief.players[fakes.player_id("WR", 1)].team
    receivers = sorted((p for p in belief.roster(team) if p.position == "WR"), key=lambda p: p.player_id)
    assert receivers[0].targets > receivers[-1].targets
    qbs = sorted((p for p in belief.roster(team) if p.position == "QB"), key=lambda p: -p.starts)
    assert qbs[0].starts > 0.5 and all(p.starts < qbs[0].starts for p in qbs[1:])


def _results_changed(data, week: int):
    """Everything after `week` changed (future_changed) and `week`'s own results too; its pre-game
    projections and tags (known before it) stay."""
    later = fakes.future_changed(data, week)
    box = {k: replace(b, rec_yds=b.rec_yds + 40, rec_td=b.rec_td + 1) if k[1] == week else b for k, b in later.box.items()}
    usage = {k: replace(u, targets=u.targets + 5) if k[1] == week else u for k, u in later.usage.items()}
    return replace(fakes.patched(later, actual={k: v + 9 for k, v in later.actual.items() if k[1] == week}),
                   box=MappingProxyType(box), usage=MappingProxyType(usage))


def test_the_belief_is_the_same_whatever_happens_from_that_week_on(data):
    assert sim_world.estimate(data, WEEK) == sim_world.estimate(_results_changed(data, WEEK), WEEK)
    assert sim_world.estimate(data, WEEK) != sim_world.estimate(_results_changed(data, WEEK - 1), WEEK)


def test_a_hard_tag_known_before_the_week_means_games_out(data):
    star = fakes.INJURED_STAR_ID
    assert sim_world.estimate(data, fakes.INJURED_WEEKS[0]).players[star].out_weeks >= 1
    assert sim_world.estimate(data, fakes.INJURED_WEEKS[0] - 1).players[star].out_weeks == 0
    ir = fakes.tagged(data, {(star, WEEK): Injury("IR", None, "Knee")})
    assert sim_world.estimate(ir, WEEK).players[star].out_weeks == sim_world.IR_BELIEF_WEEKS


def test_a_prior_world_stands_in_where_there_are_no_games(data, belief):
    preseason = replace(fakes.future_changed(data, 0), actual=MappingProxyType({}), box=MappingProxyType({}),
                        usage=MappingProxyType({}), projected=MappingProxyType({}))
    without = sim_world.estimate(preseason, 1)
    carried = sim_world.estimate(preseason, 1, prior=belief)
    wr1 = fakes.player_id("WR", 1)
    assert carried.players[wr1].targets == pytest.approx(belief.players[wr1].targets, rel=0.35)
    assert carried.players[wr1].games > without.players[wr1].games == 0


def test_the_luce_fit_recovers_shares_from_games_with_changing_active_sets():
    shares = {1: 0.5, 2: 0.3, 3: 0.2}
    games = []
    for absent in (None, 1, 2, None, 3, None) * 6:
        active = {m: s for m, s in shares.items() if m != absent}
        total = sum(active.values())
        games.append((1.0, {m: 30 * s / total for m, s in active.items()}))
    fitted = sim_world._luce(games, dict.fromkeys(shares, 1 / 3), 30.0)
    assert fitted[1] / fitted[2] == pytest.approx(shares[1] / shares[2], rel=0.15)
    assert fitted[2] / fitted[3] == pytest.approx(shares[2] / shares[3], rel=0.15)


def test_world_at_applies_dated_role_changes(belief):
    wr = fakes.player_id("WR", 5)
    changed = replace(belief, changes=(sim_world.RoleChange(10, wr, 2.0, 2.0, 1.0, "breakout"),))
    assert changed.at(9).players[wr].targets == belief.players[wr].targets
    assert changed.at(10).players[wr].targets == pytest.approx(2 * belief.players[wr].targets)
    assert changed.at(10).changes == ()
