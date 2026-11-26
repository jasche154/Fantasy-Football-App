"""sim_carryover: long injuries carried into the next season, and sim_season starting a season with them."""
from dataclasses import replace
from datetime import timedelta
from types import MappingProxyType

import pytest

from services import sim_carryover as co, sim_season, sim_world
from services.replay_pool import Injury
from tests import replay_fakes as fakes

HURT = fakes.player_id("RB", 1)
SHORT = fakes.player_id("WR", 1)
FROM = 6                         # first week on IR


@pytest.fixture(scope="module")
def data():
    season = fakes.fake_season()
    later = range(FROM, season.last_week + 1)
    season = fakes.patched(season, actual={(pid, w): None for pid in (HURT, SHORT) for w in later})
    newest = max(w for _, w in season.injuries)
    season = fakes.tagged(season, {key: None for key in season.injuries if key[1] == newest})
    return fakes.tagged(season, {(pid, w): Injury("IR", None, "Knee") for pid in (HURT, SHORT) for w in later})


def _next_week_one(data):
    return co.week_one(data) + timedelta(days=364)


def test_recovery_follows_the_table():
    assert co.recovery_weeks("Knee - ACL") == 48 and co.recovery_weeks("Knee - ACL + MCL", "Sprain") == 48
    assert co.recovery_weeks("Achilles") < 10 and co.recovery_weeks("Achilles", "Surgery") == 48
    assert co.recovery_weeks("Knee", "Surgery") == 2 * co.recovery_weeks("Knee")
    assert co.recovery_weeks("Elbow") == co.DEFAULT_WEEKS


def test_a_long_injury_is_carried_and_a_short_one_is_not(data):
    found = co.carried(data, _next_week_one(data), {HURT: ("Knee - ACL", "Surgery")})
    assert set(found) == {HURT}
    carry = found[HURT]
    assert carry.body == "Knee - ACL" and carry.hurt_week < FROM and carry.recovery == 48
    assert carry.expected > 1 and carry.gap > 30
    assert "Knee - ACL" in co.note(carry) and "expected back around week" in co.note(carry)


def test_expected_games_out_shrink_with_more_time_to_heal():
    assert co.expected_out(48, 40) > co.expected_out(48, 48) > co.expected_out(48, 60) >= 0
    assert co.games_out(48, 48, -1.0) <= co.games_out(48, 48, 0.0) <= co.games_out(48, 48, 1.0)


def test_the_truth_draw_is_seeded_and_spreads_around_the_expectation(data):
    found = co.carried(data, _next_week_one(data), {HURT: ("Knee - ACL", None)})
    assert co.draw(found, 4) == co.draw(found, 4)
    outs = [co.draw(found, seed).get(HURT) for seed in range(40)]
    lengths = [spell.games_out if spell else 0 for spell in outs]
    assert min(lengths) < max(lengths)
    assert abs(sum(lengths) / len(lengths) - found[HURT].expected) < 3


def test_a_carried_absence_starts_the_season_on_the_injury_report(data):
    empty = MappingProxyType({})
    prior = sim_world.estimate(data, data.last_week + 1)
    future = replace(data, actual=empty, projected=empty, team_by_week=empty, injuries=empty, usage=empty, box=empty,
                     expert_ranks=empty, expert_age=empty, returns=empty, notes=empty,
                     players=MappingProxyType({p: pl for p, pl in data.players.items() if pl.adp}))
    spell = co.Spell(games_out=3, exit_share=1.0, body="Knee - ACL", questionable_back=False)
    season = sim_season.simulate(future, 3, 1, prior, carried={HURT: spell})
    assert season.injuries[(HURT, 1)].hard and season.injuries[(HURT, 1)].injury == "Knee - ACL"
    team = prior.players[HURT].team
    played = [w for w in range(1, 6) if future.schedule.opponent(team, w)]
    assert all((HURT, w) not in season.actual for w in played[:3])
    assert (HURT, played[3]) in season.actual
    assert season.returns.get((HURT, 1)) == played[3]


def test_a_carried_player_the_truth_has_ready_is_cleared_before_week_one():
    """Item 34: priced as out for weeks, back for week 1 in the truth: the opener's news says so."""
    expected_out, ready = co.Carry("Knee", 15, 10.0, 7.5, 2.6), co.Carry("Ankle", 16, 4.0, 4.0, 0.2)
    drawn = {3: co.Spell(games_out=2, exit_share=1.0, body="Knee", questionable_back=False)}
    lines = co.cleared({1: expected_out, 2: ready, 3: expected_out}, drawn)
    assert lines == {(1, 1): "Ahead of schedule: cleared to play in week 1."}
