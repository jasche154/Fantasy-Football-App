"""sim_qb / sim_qb_jobs: who holds each team's QB job, measured on a season and decided week by week in one."""
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType

import numpy as np
import pytest

from services import replay_blob, replay_state, sim_next_year, sim_qb, sim_qb_fit, sim_qb_jobs, sim_season, sim_world
from services.replay_box import BoxLine
from services.sim_offseason import Rookie
from services.sim_world import Player, World
from tests import replay_fakes as fakes
from tests import test_sim_next_year as ny_tests

TEAM = "KC"                      # a bye in week BYE of the fake season
BYE = 8
WEEK = 9                         # a decision week
A, B = 1, 2                      # the holder and the other QB in the hand-made walks and decisions
OLD_BLOB = Path(__file__).parent / "data" / "sim_season_2ffb5f3.blob"   # simulate(fake_season(), 3, 16) at 2ffb5f3


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


def _team_rows(data, who, causal=False, last=17):
    rows, holders = sim_qb.walk(data, {(TEAM, w): who(w) for w in range(1, last + 1)}, causal)
    return [r for r in rows if r.team == TEAM], holders[TEAM]


def test_a_holder_loses_the_job_only_when_healthy_and_for_good(data):
    benched, holder = _team_rows(_season(data), lambda w: A if w < 6 else B)
    assert [(r.week, r.kind) for r in benched if r.lost] == [(6, "week")] and holder == (B, False)
    assert all(r.holder == A for r in benched if r.week <= 6) and all(r.holder == B for r in benched if r.week > 6)
    hurt, holder = _team_rows(_season(data, holder_tags=(6, 7)), lambda w: A if w < 6 or w > 7 else B)
    assert not any(r.lost for r in hurt) and holder == (A, False)
    assert [r.kind for r in hurt if r.week == 8] == ["return"]               # back healthy: a return decision
    assert not {6, 7} & {r.week for r in hurt}                              # a fill-in week is no decision
    back, _ = _team_rows(_season(data), lambda w: B if w in (6, 7) else A)
    assert not any(r.lost for r in back)                                    # two games, then his job again
    late, holder = _team_rows(_season(data), lambda w: A if w < 16 else B)
    assert not any(r.lost for r in late) and holder == (B, False)          # the last weeks: no decision row
    kept, holder = _team_rows(_season(data, holder_tags=(6, 7)), lambda w: A if w < 6 else B)
    assert [r.kind for r in kept if r.lost] == ["return"] and holder == (B, False)


def test_a_live_season_holder_is_judged_on_the_past_only(data):
    """causal: a healthy holder who sat while another started lost the job; a hurt one keeps it, OUT."""
    box = {k: v for k, v in data.box.items() if k[0] != A}
    box.update({(A, w): BoxLine(pass_yds=250) for w in range(1, 9)})
    season = replace(_season(data, holder_out=(9, 10)), box=MappingProxyType(box))
    _, holder = _team_rows(season.as_of(11), lambda w: A if w < 9 else B, causal=True, last=10)
    assert holder == (B, False)
    hurt = replace(_season(data, holder_out=(9, 10), holder_tags=(9, 10)), box=MappingProxyType(box))
    _, holder = _team_rows(hurt.as_of(11), lambda w: A if w < 9 else B, causal=True, last=10)
    assert holder == (A, True)


def test_the_logistic_recovers_a_known_curve_whatever_the_covariate_scale():
    dice = np.random.default_rng(0)
    x = np.column_stack([np.ones(20000), dice.normal(size=20000) * 50.0])
    y = (dice.random(20000) < 1 / (1 + np.exp(-(-3.0 + 0.016 * x[:, 1])))).astype(float)
    assert sim_qb_fit._logit(x, y, ridge=0.0) == pytest.approx([-3.0, 0.016], abs=0.1)
    assert sim_qb_fit._logit(x, y, ridge=1.0)[1] == pytest.approx(0.016, rel=0.05)   # standardized: barely shrunk


def test_established_is_the_users_two_rank_rule():
    preseason = {10: 1, 11: sim_qb.CURRENT_RANK, 12: sim_qb.CURRENT_RANK + 1, 13: 30}
    assert sim_qb.established_qbs(preseason, ()) == {10, 11}
    assert sim_qb.established_qbs(preseason, {13}) == {10, 11, 13}            # a top-8 ADP in a season before


def test_the_chances_follow_the_fit_veterans_halved_rookies_not():
    assert sim_qb.hazard(30, 0.0, 0.0) == pytest.approx(sim_qb.QB_BENCH_SCALE * sim_qb.raw_hazard(30, 0.0, 0.0))
    assert sim_qb.hazard(20, 0.0, 0.0) < sim_qb.hazard(sim_qb.UNRANKED, 0.0, 0.0)
    assert sim_qb.hazard(30, -5.0, 0.0) > sim_qb.hazard(30, 5.0, 0.0)       # playing badly: sooner
    assert sim_qb.rookie_takeover(3, 0.0, -0.3) > sim_qb.rookie_takeover(3, 0.0, 0.3)    # a losing team: sooner
    assert sim_qb.rookie_takeover(3, 0.0, 0.0) > sim_qb.rookie_takeover(60, 0.0, 0.0)    # a higher pick: sooner
    assert 1 - (1 - sim_qb.rookie_takeover(10, 0.0, -0.2)) ** 15 > 0.5      # a high pick, a losing team: usually
    assert 0 < sim_qb.return_loss(30) < sim_qb.return_loss(sim_qb.UNRANKED)


def test_a_projection_never_flips_a_qb_from_starter_to_backup():
    scoring = sim_world.scoring_of(fakes.fake_season())
    starts = [sim_world.implied_role("QB", points, scoring)[2] for points in np.arange(0.0, 25.0, 0.5)]
    assert starts == sorted(starts) and max(np.diff(starts)) < 0.15
    assert starts[0] < 0.1 < 0.9 < starts[-1]


def _world(*players):
    players = players or (Player(A, "QB", TEAM, starts=1.0), Player(B, "QB", TEAM, starts=0.1))
    return World(MappingProxyType({p.player_id: p for p in players}), MappingProxyType({}))


def _jobs(established=(), picks=None, rank=sim_qb.UNRANKED, young=None):
    return sim_qb_jobs.Jobs({TEAM: A}, {TEAM: False}, {A: rank}, frozenset(established), picks or {}, young or {})


def _changed(data, week=WEEK, seeds=300, sitting=(), **jobs):
    known, out = data.as_of(week), 0
    for seed in range(seeds):
        held = _jobs(**jobs)
        changes, _ = sim_qb_jobs.decide(held, _world(), known, week, set(sitting), {}, seed)
        out += bool(changes)
        assert held.holders[TEAM] == (B if changes else A)
    return out / seeds


def test_a_weekly_benching_follows_the_hazard_and_never_hits_an_established_starter(data, monkeypatch):
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.6)       # x 0.5: 0.3 a week
    assert _changed(data) == pytest.approx(0.3, abs=0.07)
    assert _changed(data, established={A}) == 0.0
    assert _changed(data, sitting=(B,)) == 0.0                                       # nobody healthy to take over


def test_no_decision_is_rolled_on_a_bye(data, monkeypatch):
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 1.0)
    assert not data.schedule.opponent(TEAM, BYE)
    assert _changed(data, week=BYE, seeds=50) == 0.0 and _changed(data, seeds=50) > 0.3


def test_a_drafted_rookie_takes_over_from_a_weak_starter_only_at_the_unscaled_chance(data, monkeypatch):
    monkeypatch.setattr(sim_qb, "rookie_takeover", lambda pick, form, record: 0.4)
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.0)
    assert _changed(data, picks={B: 5}) == pytest.approx(0.4, abs=0.07)            # not halved
    assert _changed(data, picks={B: 5}, established={A}) == 0.0                      # behind an established one: sits
    assert _changed(data, picks={B: sim_qb.ROOKIE_PICKS + 1}) == 0.0                # a late pick: the veteran path


def test_a_young_first_rounder_loses_no_job_to_a_rookie_nor_any_as_a_rookie(data, monkeypatch):
    """Item 24: real 2019-2025, a round-1 QB holding the job in his first season was never benched healthy, and
    no team with one drafted a QB early."""
    monkeypatch.setattr(sim_qb, "rookie_takeover", lambda pick, form, record: 0.6)
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.6)
    season = data.season
    assert _changed(data, picks={B: 5}, young={A: season - 1}) == pytest.approx(0.3, abs=0.07)   # the veteran path
    assert _changed(data, young={A: season}) == 0.0                                  # a first-round rookie holder
    assert _changed(data, picks={B: 5}, young={A: season - 3}) == pytest.approx(0.3, abs=0.07)


def test_a_young_first_rounder_back_from_injury_keeps_his_job_from_a_rookie(data, monkeypatch):
    known = data.as_of(WEEK + 1)
    monkeypatch.setattr(sim_qb, "starters", lambda known: {(TEAM, WEEK): B})
    monkeypatch.setattr(sim_qb, "return_loss", lambda rank: 1.0)
    for young, picks, holder in (({A: data.season - 2}, {B: 30}, A), ({A: data.season - 2}, {}, B), ({A: data.season}, {}, A)):
        jobs = _jobs(picks=picks, young=young)
        sim_qb_jobs.decide(jobs, _world(), known, WEEK, {A}, {}, 1)
        sim_qb_jobs.decide(jobs, _world(), known, WEEK + 1, set(), {}, 1)
        assert jobs.holders[TEAM] == holder


def test_a_job_change_gets_its_true_line_the_week_it_happens(data):
    known = data.as_of(WEEK)
    jobs = _jobs()
    jobs.holders[TEAM] = B
    names = {A: "Old Starter", B: "New Starter"}
    healthy = sim_qb_jobs.news([sim_world.RoleChange(WEEK, A, kind="bench")], jobs, _world(), known, WEEK, names)
    assert healthy == {(A, WEEK): "Benched: New Starter starts in his place.", (B, WEEK): "Named the starter over Old Starter."}
    hurt = replace(known, injuries=MappingProxyType({**known.injuries, (A, WEEK - 1): fakes.Injury("O")}))
    back = sim_qb_jobs.news([sim_world.RoleChange(WEEK, A, kind="bench")], jobs, _world(), hurt, WEEK, names)
    assert back[(B, WEEK)].startswith("Stays the starter") and "healthy again" in back[(A, WEEK)].lower()
    # review item 2: hurt two weeks ago, played healthy last week, benched now: a healthy benching
    week = WEEK + 2                                                          # weeks 9 and 10 are both KC games
    later = data.as_of(week)
    earlier = replace(later, injuries=MappingProxyType({**later.injuries, (A, week - 2): fakes.Injury("O")}),
                      box=MappingProxyType({**later.box, (A, week - 1): BoxLine(pass_yds=200)}))
    played = sim_qb_jobs.news([sim_world.RoleChange(week, A, kind="bench")], jobs, _world(), earlier, week, names)
    assert played[(A, week)].startswith("Benched")
    idle = replace(earlier, box=MappingProxyType({k: v for k, v in earlier.box.items() if k != (A, week - 1)}))
    assert sim_qb_jobs.news([sim_world.RoleChange(week, A, kind="bench")], jobs, _world(), idle, week,
                            names)[(A, week)].startswith("Healthy again")


def test_the_benched_holder_is_never_rumoured_the_week_he_loses_the_job(data, monkeypatch):
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.6)
    for seed in range(40):
        held = _jobs()
        changes, rumors = sim_qb_jobs.decide(held, _world(), data.as_of(WEEK), WEEK, set(), {}, seed)
        assert not (changes and A in rumors)


def test_a_job_danger_hint_comes_the_week_before_the_benching_it_foretells(data, monkeypatch):
    """Review item 3: the holders decide marks in danger are the ones next week's die benches (at a steady chance)."""
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.3)
    warned = foretold = benched_unwarned = 0
    for seed in range(300):
        held = _jobs()
        sim_qb_jobs.decide(held, _world(), data.as_of(WEEK), WEEK, set(), {}, seed)
        if held.holders[TEAM] != A:
            continue                                         # benched this week already
        danger = A in held.in_danger
        changes, _ = sim_qb_jobs.decide(held, _world(), data.as_of(WEEK + 1), WEEK + 1, set(), {}, seed)
        warned += danger
        foretold += danger and bool(changes)
        benched_unwarned += (not danger) and bool(changes)
    assert warned > 10 and foretold == warned and benched_unwarned == 0
    hinted = 0
    for seed in range(100):                                  # review item 1: nobody healthy to take over, no danger
        held = _jobs()
        sim_qb_jobs.decide(held, _world(), data.as_of(WEEK), WEEK, set(), {}, seed)
        hinted += bool(held.in_danger)
        held = _jobs()
        sim_qb_jobs.decide(held, _world(), data.as_of(WEEK), WEEK, {B}, {}, seed)
        assert not held.in_danger
    assert hinted                                            # the same seeds do warn when B is healthy


def test_a_hurt_holder_sits_then_gets_the_job_back_unless_a_weak_one_loses_it(data, monkeypatch):
    known = data.as_of(WEEK + 1)
    monkeypatch.setattr(sim_qb, "starters", lambda known: {(TEAM, WEEK): B})
    monkeypatch.setattr(sim_qb, "return_loss", lambda rank: 1.0)
    for established, holder in (({A}, A), ((), B)):
        jobs = _jobs(established)
        sim_qb_jobs.decide(jobs, _world(), known, WEEK, {A}, {}, 1)             # hurt: he sits, B fills in
        assert jobs.holders[TEAM] == A and jobs.out[TEAM]
        sim_qb_jobs.decide(jobs, _world(), known, WEEK + 1, set(), {}, 1)       # healthy again
        assert jobs.holders[TEAM] == holder and not jobs.out[TEAM]


def test_the_holder_starts_whenever_he_dresses():
    world = sim_qb_jobs.apply(_world(Player(A, "QB", TEAM, starts=0.2), Player(B, "QB", TEAM, starts=0.9)), _jobs())
    assert world.players[A].starts > world.players[B].starts == 0.9


def _untagged_absence(data):
    """The reviewer's case: the ADP-rank-1 QB misses weeks 9-10 with no tag, his backup starts them."""
    ranks = sim_qb.market_ranks(data)
    star = min(ranks, key=ranks.get)
    team = data.team_at(star, 5)
    backup = next(p for p, pl in data.players.items() if pl.position == "QB" and p != star and data.team_at(p, 5) == team)
    box = {k: replace(v, pass_yds=250) if k[0] == star else v for k, v in data.box.items()   # the fake's QBs throw
           if k not in ((star, 9), (star, 10))}
    box.update({(backup, w): BoxLine(pass_yds=260) for w in (9, 10)})
    games = {**data.team_by_week, star: tuple((w, t) for w, t in data.team_by_week[star] if w not in (9, 10)),
             backup: tuple(sorted({*data.team_by_week.get(backup, ()), (9, team), (10, team)}))}
    actual = {k: v for k, v in data.actual.items() if k not in ((star, 9), (star, 10))}
    actual.update({(backup, 9): 15.0, (backup, 10): 15.0})
    injuries = {k: v for k, v in data.injuries.items() if k[0] != star}
    return replace(data, box=MappingProxyType(box), team_by_week=MappingProxyType(games),
                   actual=MappingProxyType(actual), injuries=MappingProxyType(injuries)), star, team


def test_an_established_qb_who_sat_untagged_is_still_the_holder_of_a_live_start(data):
    season, star, team = _untagged_absence(data)
    established = sim_qb_jobs.established_of(season, None, False, sim_qb_jobs.QbInputs())
    assert star in established
    jobs = sim_qb_jobs.start(season, sim_world.estimate(season, 11), 11, established)
    assert jobs.holders[team] == star and jobs.out[team]                     # OUT, never benched
    for seed in range(4):
        sim = sim_season.simulate(season, seed, 11)
        starts = sim_qb.starters(sim)
        played = [w for w in range(11, 18) if (team, w) in starts and (star, w) in sim.actual]
        assert played and all(starts[(team, w)] == star or (star, w + 1) in sim.injuries for w in played), seed


def test_a_backup_established_only_by_an_old_adp_does_not_take_a_live_job(data):
    """2026 IND: Anthony Richardson (a top-8 ADP in 2024) is established, but Daniel Jones has held the job all
    season: Jones is the holder at a live start."""
    season, star, team = _untagged_absence(data)
    backup = next(p for p, pl in season.players.items() if pl.position == "QB" and p != star and season.team_at(p, 5) == team)
    healthy = replace(season, box=MappingProxyType({**data.box, **{(star, w): replace(data.box[(star, w)], pass_yds=250)
                                                                   for w in range(1, 11) if (star, w) in data.box}}))
    jobs = sim_qb_jobs.start(healthy, sim_world.estimate(healthy, 11), 11, frozenset({backup}))
    assert jobs.holders[team] == star


def test_a_future_stars_carried_injury_does_not_cost_him_his_protection(data):
    """A season simulated whole ranks on per-game value, nobody believed out: an ADP discounted for a long
    injury carried into it does not drop him out of the established range."""
    belief = sim_world.estimate(data, data.last_week + 1)
    ranks = sim_qb.preseason_ranks(belief)
    star = min(ranks, key=ranks.get)
    hurt = replace(belief, players=MappingProxyType({**belief.players, star: replace(belief.players[star], out_weeks=12)}))
    cheap = replace(data, players=MappingProxyType({**data.players, star: replace(data.players[star], adp=400.0)}))
    assert star in sim_qb_jobs.established_of(cheap, hurt, True, sim_qb_jobs.QbInputs())
    assert star not in sim_qb_jobs.established_of(cheap, hurt, False, sim_qb_jobs.QbInputs())   # a real ADP would drop him


def test_a_starters_claim_counts_the_games_he_missed_hurt(data):
    """starts_by_team counts the weeks a starter missed with a tag, so restore_starters keeps him in August."""
    season, star, team = _untagged_absence(data)
    tagged = replace(season, injuries=MappingProxyType({**season.injuries, (star, 9): fakes.Injury("IR"),
                                                        (star, 10): fakes.Injury("IR")}))
    assert {(9, team), (10, team)} <= set(sim_next_year.starts_by_team(tagged)[star])
    assert not {(9, team), (10, team)} & set(sim_next_year.starts_by_team(season)[star])   # untagged: no claim


def test_august_uses_the_same_established_rule(monkeypatch):
    """sim_next_year names a rookie over an incumbent only when sim_qb.established_qbs says he is not established
    (one definition: here every QB ranks 30th, so the incumbent is protected only by a top-8 ADP before)."""
    monkeypatch.setattr(sim_qb, "preseason_ranks", lambda world, record=None: {p: 30 for p, pl in world.players.items()
                                                                  if pl.position == "QB"})
    first_pick = Rookie("Franchise Passer", "QB", "PIT", 1, "Ohio State")
    rookie = sim_next_year.ROOKIE_ID_BASE + 2027 * sim_next_year.ROOKIE_ID_SEASON + 1
    world = ny_tests._world()

    def august(past):
        return sim_next_year.next_year(world, ny_tests._names(world), dict.fromkeys(world.players, 3),
                                       sim_next_year.rookie_ids(2027, (first_pick,)), {}, None, past)[0]
    assert august(frozenset()).players[rookie].starts == 1.0
    kept = august(frozenset({ny_tests.pid("PIT", "QB1")}))
    assert kept.players[ny_tests.pid("PIT", "QB1")].starts == 1.0 > kept.players[rookie].starts


def test_holders_and_their_decisions_read_nothing_from_the_week_on(data):
    """No hindsight: the live holders before week W and week W's decision are the same whatever happens from W on."""
    changed = fakes.future_changed(data, WEEK - 1)
    belief = sim_world.estimate(data, WEEK)
    established = sim_qb_jobs.established_of(data, None, False, sim_qb_jobs.QbInputs())
    assert established == sim_qb_jobs.established_of(changed, None, False, sim_qb_jobs.QbInputs())
    for seed in range(20):
        jobs = [sim_qb_jobs.start(d, belief, WEEK, established) for d in (data, changed)]
        out = [sim_qb_jobs.decide(j, belief, d.as_of(WEEK), WEEK, set(), {}, seed) for j, d in zip(jobs, (data, changed))]
        assert out[0] == out[1] and jobs[0] == jobs[1]


def test_the_belief_keeps_a_starter_who_missed_games_hurt_and_counts_starts_for_another_team():
    line = BoxLine(pass_yds=250)
    keys = ((TEAM, 1), (TEAM, 2), (TEAM, 3), (TEAM, 4), ("LV", 1), ("LV", 2))
    seen = sim_world._Seen(games={(TEAM, 1): {A: line}, (TEAM, 2): {B: line}, (TEAM, 3): {B: line},
                                  (TEAM, 4): {A: line}, ("LV", 1): {7: line}, ("LV", 2): {7: line}},
                           targets={}, kicks={}, pass_yards=dict.fromkeys(keys, 250.0), hurt=frozenset({(A, 2), (A, 3)}))
    weights = sim_qb.qb_starts(TEAM, seen, {A: 0.5, B: 0.5, 7: 0.05}, 5)
    healthy = sim_qb.qb_starts(TEAM, replace(seen, hurt=frozenset()), {A: 0.5, B: 0.5, 7: 0.05}, 5)
    assert weights[A] > weights[B] and weights[A] > healthy[A]             # weeks missed hurt don't count against him
    assert weights[7] > 0.2 > 0.05                                         # LV's starter traded in keeps his record


def test_a_simulated_season_is_deterministic_and_never_takes_an_established_starters_job(data):
    first = sim_season.simulate(data, 4, 8)
    assert first.actual == sim_season.simulate(data, 4, 8).actual
    established = sim_qb_jobs.established_of(data, None, False, sim_qb_jobs.QbInputs())
    assert established                                      # else the last check holds for nothing
    rows = [r for seed in range(3) for r in sim_qb.walk(sim_season.simulate(data, seed, 8), established=established)[0]
            if r.week >= 8]
    assert {"week", "return"} <= {r.kind for r in rows}
    assert not [r for r in rows if r.lost and r.established]               # neither benched nor kept out on return


def test_a_season_frozen_before_this_change_still_loads():
    """A blob written by the code at 2ffb5f3 (tests/data, simulate(fake_season(), 3, 16)) loads unchanged."""
    season = replay_blob.loads(OLD_BLOB.read_bytes())
    assert len(season.actual) == 5669 and sum(season.actual.values()) == pytest.approx(44476.1)
    assert season.as_of(17).known_before == 17 and sim_qb.starters(season)


def test_rng_streams_are_per_week_and_team():
    assert replay_state.rng(1, "sim", "qb", 5, TEAM).random() != replay_state.rng(1, "sim", "qb", 6, TEAM).random()
