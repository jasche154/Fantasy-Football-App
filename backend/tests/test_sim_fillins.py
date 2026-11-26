"""Fill-in quarterbacks: the experts' view of one (sim_market.expert_view) and the measure (sim_qb_fillins)."""
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from services import sim_market, sim_qb_fillins, sim_qb_jobs, sim_world
from services.replay_box import BoxLine
from services.scoring import scoring_for
from tests import replay_fakes as fakes

WEEK = 9
HALF = scoring_for("half")


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def _hurt_holder(data, quality: float, out_weeks: int = 2):
    """The week-9 belief with one team's holder out `out_weeks` games and his backup passing at `quality`."""
    belief = sim_world.estimate(data, WEEK)
    team = next(t for t in sorted(belief.teams) if sum(p.position == "QB" for p in belief.roster(t)) > 1)
    holder, backup = sorted((p for p in belief.roster(team) if p.position == "QB"), key=lambda p: -p.starts)[:2]
    players = {**belief.players, holder.player_id: replace(holder, out_weeks=out_weeks),
               backup.player_id: replace(backup, quality=quality)}
    world = sim_qb_jobs.apply(replace(belief, players=MappingProxyType(players)),
                              sim_qb_jobs.Jobs({team: holder.player_id}, {team: True}, {}))
    return world, holder.player_id, backup.player_id


def test_the_fill_in_is_the_healthy_qb_behind_a_holder_believed_out(data):
    world, holder, backup = _hurt_holder(data, 1.0)
    assert sim_market.fill_ins(world) == {backup}
    healthy = replace(world, players=MappingProxyType({**world.players, holder: replace(world.players[holder], out_weeks=0)}))
    assert not sim_market.fill_ins(healthy)


def test_the_public_trusts_only_part_of_a_hot_fill_ins_passing_and_never_rates_a_cold_one_up(data):
    trust, backup_level = sim_market.opinion_knob("fill_in_trust"), sim_world.params()["league"]["backup_quality"]
    assert 0.0 <= trust < 1.0
    hot, _, pid = _hurt_holder(data, 1.3)
    assert sim_market.fill_in_view(hot).players[pid].quality == pytest.approx(backup_level + trust * (1.3 - backup_level))
    cold, _, pid = _hurt_holder(data, 0.8)
    assert sim_market.fill_in_view(cold).players[pid].quality == 0.8
    healthy, holder, pid = _hurt_holder(data, 1.3, out_weeks=0)
    assert sim_market.fill_in_view(healthy) is healthy                       # no fill-in: the belief as it is


def test_held_back_moves_only_the_chosen_down_to_the_floor():
    assert sim_market.held_back([1, 2, 3, 4, 5], {1, 4}, 3) == [2, 3, 1, 4, 5]
    assert sim_market.held_back([1, 2, 3], {3}, 2) == [1, 2, 3]
    assert sim_market.held_back([1, 2], {1}, 5) == [2, 1]


def test_a_hot_fill_in_is_never_an_expert_top_qb_while_his_holder_is_out(data):
    """The user's ruling: a fill-in is never priced like a top QB on the strength of a few starts. Weekly experts
    rank him no better than FILL_IN_BEST; the preseason market (ADP) is left alone."""
    world, holder, pid = _hurt_holder(data, 1.6, out_weeks=data.last_week)
    games_left = data.last_week - WEEK + 1
    ranks = sim_market.expert_ranks(world, world.players, WEEK, np.random.default_rng(0), HALF, games_left)
    worth = sim_market.values(world, HALF, games_left)
    by_value = sorted((p for p in worth if world.players[p].position == "QB"), key=lambda p: -worth[p])
    assert by_value.index(pid) + 1 < sim_market.FILL_IN_BEST                 # his value alone would rank him higher
    assert ranks[(pid, WEEK)] == sim_market.FILL_IN_BEST
    healthy = replace(world, players=MappingProxyType({**world.players, holder: replace(world.players[holder], out_weeks=0)}))
    backup = sim_market.expert_ranks(healthy, healthy.players, WEEK, np.random.default_rng(0), HALF, games_left)
    assert backup[(pid, WEEK)] > sim_market.FILL_IN_BEST                     # a backup who does not start: no floor needed


def test_the_measure_finds_a_fill_in_run_and_ranks_after_his_starts(data):
    """A run is three or more starts by a QB other than the holder while the holder carries a tag."""
    qbs = sorted((pid for pid, p in data.players.items() if p.position == "QB"), key=lambda pid: data.players[pid].adp or 999)
    holder = qbs[0]
    team = data.team_at(holder, 1)
    backup = next(pid for pid in qbs[1:] if data.team_at(pid, 1) == team)
    played = [w for w, t in data.team_by_week[holder]]
    run = played[4:8]
    box = {**data.box, **{(holder, w): BoxLine(pass_yds=250) for w in played if w not in run},
           **{(backup, w): BoxLine(pass_yds=300) for w in run}}
    box = {k: v for k, v in box.items() if not (k[0] == holder and k[1] in run)}
    games = {**data.team_by_week, holder: tuple((w, t) for w, t in data.team_by_week[holder] if w not in run),
             backup: tuple(sorted({*data.team_by_week.get(backup, ()), *((w, team) for w in run)}))}
    season = replace(data, box=MappingProxyType(box), team_by_week=MappingProxyType(games),
                     injuries=MappingProxyType({**data.injuries, **{(holder, w): fakes.Injury("O") for w in run}}),
                     actual=MappingProxyType({**data.actual, **{(backup, w): 20.0 for w in run}}))
    found = sim_qb_fillins.runs(season)
    assert [(r.player, r.weeks, r.ppg, r.holder_back) for r in found] == [(backup, tuple(run), 20.0, True)]
    assert set(found[0].projection_rank) == {2, 3, 4}
