"""sim_qb / sim_qb_jobs: who holds each team's QB job, measured on a season and decided week by week in one."""
from dataclasses import replace
from types import MappingProxyType

import numpy as np
import pytest

from services import replay_state, sim_qb, sim_qb_fit, sim_qb_jobs, sim_season, sim_world
from services.replay_box import BoxLine
from services.sim_world import Player, World
from tests import replay_fakes as fakes

TEAM = "KC"
A, B = 1, 2                      # the holder and the other QB in the hand-made walks


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def _season(data, holder_out=(), holder_tags=()):
    """`data` with A and B on TEAM (no games of their own unless kept) and A's tags in holder_tags only."""
    players = {**data.players, A: fakes.PoolPlayer(A, "Holder", "QB", 50.0), B: fakes.PoolPlayer(B, "Other", "QB")}
    games = {**data.team_by_week, A: tuple((w, TEAM) for w in range(1, 18) if w not in holder_out)}
    tags = {key: v for key, v in data.injuries.items() if key[0] not in (A, B)}
    tags.update({(A, w): fakes.Injury("O") for w in holder_tags})
    return replace(data, players=MappingProxyType(players), team_by_week=MappingProxyType(games),
                   injuries=MappingProxyType(tags))


def _starts(who, last=17):
    return {(TEAM, w): who(w) for w in range(1, last + 1)}


def _team_rows(data, who, causal=False, last=17):
    rows, holders = sim_qb.walk(data, _starts(who, last), causal)
    return [r for r in rows if r.team == TEAM], holders[TEAM]


def test_a_holder_loses_the_job_only_when_healthy_and_for_good(data):
    benched, holder = _team_rows(_season(data), lambda w: A if w < 6 else B)
    assert [(r.week, r.kind) for r in benched if r.lost] == [(6, "week")] and holder == (B, False)
    assert all(r.holder == A for r in benched if r.week <= 6) and all(r.holder == B for r in benched if r.week > 6)
    hurt, holder = _team_rows(_season(data, holder_tags=(6, 7)), lambda w: A if w < 6 or w > 7 else B)
    assert not any(r.lost for r in hurt) and holder == (A, False)
    assert [r.week for r in hurt if r.kind == "return"] == [8]              # back healthy: a return decision
    assert 6 not in {r.week for r in hurt} and 7 not in {r.week for r in hurt}     # a fill-in week is no decision
    back, _ = _team_rows(_season(data), lambda w: B if w in (6, 7) else A)
    assert not any(r.lost for r in back)                                    # two games, then his job again
    late, holder = _team_rows(_season(data), lambda w: A if w < 16 else B)
    assert not any(r.lost for r in late) and holder == (B, False)          # the last weeks: no decision row
    kept, holder = _team_rows(_season(data, holder_tags=(6, 7)), lambda w: A if w < 6 else B)
    assert [(r.week, r.kind) for r in kept if r.lost] == [(8, "return")] and holder == (B, False)


def test_a_live_season_holder_is_judged_on_the_past_only(data):
    """causal: a healthy holder who sat while another started lost the job; a hurt one keeps it, OUT."""
    season = _season(data, holder_out=(9, 10))
    box = {k: v for k, v in season.box.items() if k[0] != A}
    box.update({(A, w): BoxLine(pass_yds=250) for w in range(1, 9)})
    season = replace(season, box=MappingProxyType(box))
    _, holder = _team_rows(season.as_of(11), lambda w: A if w < 9 else B, causal=True, last=10)
    assert holder == (B, False)
    hurt = _season(data, holder_out=(9, 10), holder_tags=(9, 10))
    _, holder = _team_rows(replace(hurt, box=MappingProxyType(box)).as_of(11), lambda w: A if w < 9 else B, causal=True,
                           last=10)
    assert holder == (A, True)


def test_the_logistic_recovers_a_known_curve():
    dice = np.random.default_rng(0)
    x = np.column_stack([np.ones(20000), dice.normal(size=20000)])
    y = (dice.random(20000) < 1 / (1 + np.exp(-(-3.0 + 0.8 * x[:, 1])))).astype(float)
    assert sim_qb_fit._logit(x, y, ridge=0.0) == pytest.approx([-3.0, 0.8], abs=0.12)


def test_an_established_starter_never_loses_his_job_and_the_rest_at_half_the_real_rate():
    cut = sim_qb.stored()["established_rank"]
    assert sim_qb.established(1) and sim_qb.established(cut) and not sim_qb.established(cut + 1)
    assert sim_qb.hazard(cut, -10.0, -0.5) == 0.0 == sim_qb.return_loss(1)
    chances = [sim_qb.hazard(rank, 0.0, 0.0) for rank in range(cut + 1, sim_qb.UNRANKED + 1)]
    assert chances == sorted(chances) and chances[0] > 0
    assert sim_qb.hazard(30, 0.0, 0.0) == pytest.approx(sim_qb.QB_BENCH_SCALE * sim_qb.raw_hazard(30, 0.0, 0.0))
    assert sim_qb.hazard(30, -5.0, -0.3) > sim_qb.hazard(30, 5.0, 0.3)     # playing badly on a losing team: sooner
    assert 0 < sim_qb.return_loss(30) < sim_qb.return_loss(sim_qb.UNRANKED)


def test_a_projection_never_flips_a_qb_from_starter_to_backup():
    scoring = sim_world.scoring_of(fakes.fake_season())
    starts = [sim_world.implied_role("QB", points, scoring)[2] for points in np.arange(0.0, 25.0, 0.5)]
    assert starts == sorted(starts) and max(np.diff(starts)) < 0.15
    assert starts[0] < 0.1 < 0.9 < starts[-1]


def _world(holder_rank_players=(Player(A, "QB", TEAM, starts=1.0), Player(B, "QB", TEAM, starts=0.1))):
    return World(MappingProxyType({p.player_id: p for p in holder_rank_players}), MappingProxyType({}))


def _benched(rank, data, seeds=300, sitting=()):
    known = data.as_of(8)
    out = 0
    for seed in range(seeds):
        jobs = sim_qb_jobs.Jobs({TEAM: A}, {TEAM: False}, {A: rank})
        changes, _ = sim_qb_jobs.decide(jobs, _world(), known, 8, set(sitting), {}, seed)
        out += bool(changes)
        assert jobs.holders[TEAM] == (B if changes else A)
    return out / seeds


def test_a_weekly_benching_follows_the_hazard_and_never_hits_an_established_starter(data, monkeypatch):
    monkeypatch.setattr(sim_qb, "hazard", lambda rank, form, record: 0.0 if sim_qb.established(rank) else 0.3)
    assert _benched(1, data) == 0.0
    assert _benched(sim_qb.UNRANKED, data) == pytest.approx(0.3, abs=0.07)
    assert _benched(sim_qb.UNRANKED, data, sitting=(B,)) == 0.0             # nobody healthy to take over


def test_a_hurt_holder_sits_then_gets_the_job_back_unless_a_weak_one_loses_it(data, monkeypatch):
    known = data.as_of(8)
    starts = {(TEAM, 7): B}
    monkeypatch.setattr(sim_qb, "starters", lambda known: starts)
    monkeypatch.setattr(sim_qb, "return_loss", lambda rank: 0.0 if sim_qb.established(rank) else 1.0)
    for rank, holder in ((1, A), (sim_qb.UNRANKED, B)):
        jobs = sim_qb_jobs.Jobs({TEAM: A}, {TEAM: False}, {A: rank})
        sim_qb_jobs.decide(jobs, _world(), known, 7, {A}, {}, 1)            # hurt: he sits, B fills in
        assert jobs.holders[TEAM] == A and jobs.out[TEAM]
        sim_qb_jobs.decide(jobs, _world(), known, 8, set(), {}, 1)          # healthy again
        assert jobs.holders[TEAM] == holder and not jobs.out[TEAM]


def test_the_holder_starts_whenever_he_dresses():
    world = sim_qb_jobs.apply(_world((Player(A, "QB", TEAM, starts=0.2), Player(B, "QB", TEAM, starts=0.9))),
                              sim_qb_jobs.Jobs({TEAM: A}, {TEAM: False}, {}))
    assert world.players[A].starts > world.players[B].starts == 0.9


def test_holders_and_their_decisions_read_nothing_from_the_week_on(data):
    """No hindsight: the live holders before week W and week W's decision are the same whatever happens from W on."""
    week = 9
    changed = fakes.future_changed(data, week - 1)
    belief = sim_world.estimate(data, week)
    first, second = (sim_qb_jobs.start(d, belief, week) for d in (data, changed))
    assert first == second
    for seed in range(20):
        jobs = [sim_qb_jobs.start(d, belief, week) for d in (data, changed)]
        out = [sim_qb_jobs.decide(j, belief, d.as_of(week), week, set(), {}, seed) for j, d in zip(jobs, (data, changed))]
        assert out[0] == out[1] and jobs[0] == jobs[1]


def test_the_belief_keeps_a_starter_who_missed_games_hurt_and_counts_starts_for_another_team():
    line = BoxLine(pass_yds=250)
    seen = sim_world._Seen(
        games={(TEAM, 1): {A: line}, (TEAM, 2): {B: line}, (TEAM, 3): {B: line}, (TEAM, 4): {A: line},
               ("LV", 1): {7: line}, ("LV", 2): {7: line}},
        targets={}, kicks={}, pass_yards={k: 250.0 for k in ((TEAM, 1), (TEAM, 2), (TEAM, 3), (TEAM, 4), ("LV", 1), ("LV", 2))},
        hurt=frozenset({(A, 2), (A, 3)}))
    weights = sim_world._qb_starts(TEAM, seen, {A: 0.5, B: 0.5, 7: 0.05}, 5)
    healthy = sim_world._qb_starts(TEAM, replace(seen, hurt=frozenset()), {A: 0.5, B: 0.5, 7: 0.05}, 5)
    assert weights[A] > weights[B] and weights[A] > healthy[A]             # weeks missed hurt don't count against him
    assert weights[7] > 0.2 > 0.05                                         # LV's starter traded in keeps his record


def test_a_simulated_season_is_deterministic_and_benches_no_established_starter(data):
    first = sim_season.simulate(data, 4, 8)
    assert first.actual == sim_season.simulate(data, 4, 8).actual
    ranks = sim_qb.market_ranks(first)
    rows = [r for seed in range(3) for r in sim_qb.walk(sim_season.simulate(data, seed, 8))[0] if r.week >= 8]
    assert not [r for r in rows if r.lost and r.kind == "week" and sim_qb.established(ranks.get(r.holder, 40))]


def test_old_blobs_need_no_new_field(data):
    """The holders live only inside simulate(): a frozen season (replay_blob) carries no new field to default."""
    from services import replay_blob

    blob, season = replay_blob.freeze(sim_season.simulate(data, 2, 15))
    assert replay_blob.loads(blob).actual == season.actual


def test_rng_streams_are_per_week_and_team():
    assert replay_state.rng(1, "sim", "qb", 5, TEAM).random() != replay_state.rng(1, "sim", "qb", 6, TEAM).random()
