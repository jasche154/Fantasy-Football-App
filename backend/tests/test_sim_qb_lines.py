"""The QB job holder (sim_qb_jobs) and the pregame line (sim_lines) together: a week's line, its projections and
its game are all built on the week's holder, or his replacement when he is benched or hurt."""
import pytest

from services import sim_game, sim_lines, sim_market, sim_qb, sim_qb_jobs, sim_season, sim_world
from tests import replay_fakes as fakes

FROM_WEEK = 6
SEEDS = range(3)


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def _spy(monkeypatch) -> dict:
    """Record, per simulated week, the holders after the decision, the belief the line and the projections are
    built on, and the truth each game is played on."""
    seen: dict = {"holders": {}, "lines": {}, "projections": {}, "games": []}
    decide, week_lines, projections, play = sim_qb_jobs.decide, sim_lines.week_lines, sim_market.projections, sim_season.play

    def spy_decide(jobs, world, known, week, sitting, tags, seed):
        found = decide(jobs, world, known, week, sitting, tags, seed)
        seen["holders"][week] = dict(jobs.holders)
        return found

    def spy_lines(known, week, belief, out, *args, **kwargs):
        seen["lines"][week] = (belief, set(out))
        return week_lines(known, week, belief, out, *args, **kwargs)

    def spy_projections(belief, schedule, week, out, *args, **kwargs):
        seen["projections"][week] = belief
        return projections(belief, schedule, week, out, *args, **kwargs)

    def spy_play(world, home, away, out, *args, **kwargs):
        seen["games"].append((max(seen["holders"]), world, home, away, set(out)))
        return play(world, home, away, out, *args, **kwargs)

    monkeypatch.setattr(sim_qb_jobs, "decide", spy_decide)
    monkeypatch.setattr(sim_lines, "week_lines", spy_lines)
    monkeypatch.setattr(sim_market, "projections", spy_projections)
    monkeypatch.setattr(sim_season, "play", spy_play)
    return seen


def _starter(world, team: str, opponent: str, out) -> int | None:
    return sim_game._side_of(world, team, opponent, out).qb


def _changes(holders: dict) -> int:
    """How many times a team's holder changed from one simulated week to the next."""
    weeks = sorted(holders)
    return sum(holders[w][team] != holders[prev].get(team, holders[w][team])
               for prev, w in zip(weeks, weeks[1:]) for team in holders[w])


def test_the_line_projections_and_game_use_the_weeks_holder_or_his_replacement(data, monkeypatch):
    """A holder benched in week W (or a fill-in kept on his return) is replaced in W's line, projections and game
    and in every week after; a hurt holder sits in all three and the next QB in line is the starter."""
    monkeypatch.setattr(sim_qb, "raw_hazard", lambda rank, form, record: 0.5)   # benchings every few weeks
    monkeypatch.setattr(sim_qb, "established_qbs", lambda ranks, past: frozenset())   # nobody is protected
    changed = hurt = 0
    seen = _spy(monkeypatch)
    for seed in SEEDS:
        for record in seen.values():
            record.clear()
        sim_season.simulate(data, seed, FROM_WEEK)
        changed += _changes(seen["holders"])
        for week, holders in sorted(seen["holders"].items()):
            belief, public_out = seen["lines"][week]
            assert seen["projections"][week] is belief                  # the line's belief projects the week
            for game in (g for g in data.schedule.games if g.week == week):
                for team, opponent in ((game.home_team, game.away_team), (game.away_team, game.home_team)):
                    holder = holders.get(team)
                    if holder is None:
                        continue
                    line_qb = _starter(belief, team, opponent, public_out)
                    if holder in public_out:
                        hurt += 1
                        assert line_qb != holder, (seed, week, team)
                    else:
                        assert line_qb == holder, (seed, week, team)
        for week, world, home, away, sitting in seen["games"]:
            holders = seen["holders"][week]
            for team, opponent in ((home, away), (away, home)):
                if team in holders:
                    starter = _starter(world, team, opponent, sitting)
                    assert (starter == holders[team]) == (holders[team] not in sitting), (seed, week, team)
    assert changed and hurt, (changed, hurt)                             # both cases were exercised


def test_experts_revalue_a_teams_quarterbacks_at_once_on_a_benching(data):
    """A benching is news like an injury: both of the team's QBs are re-ranked on this week's value in full."""
    belief = sim_world.estimate(data, FROM_WEEK)
    qbs = [p for p in belief.players.values() if p.position == "QB"]
    team = next(t for t in sorted(belief.teams) if sum(p.team == t for p in qbs) > 1)
    old, new = sorted((p.player_id for p in qbs if p.team == team), key=lambda pid: -belief.players[pid].starts)[:2]
    before, now = {old: 18.0, new: 2.0}, {old: 1.0, new: 17.0}
    step = sim_market.opinion_knob("worth_step")
    assert sim_market.held(before, now, belief)[new] == pytest.approx(2.0 + step * 15.0)    # no news: slowly
    assert sim_market.held(before, now, belief, benched=[old]) == {old: 1.0, new: 17.0}
