"""Conservative learning of the Week X source weights from actual results."""
import json

import pytest

from database.models import LearnedWeights, NflGame, PlayerStatsWeekly, ProjectionHistory
from services import weight_learning as wl
from services.weight_learning import DEFAULT_WEIGHTS, Sample


def test_fit_puts_weight_on_the_source_that_predicted_best():
    samples = [Sample({"model": a, "espn": a + (6 if i % 2 else -6)}, a) for i, a in enumerate(range(5, 25))]
    fitted = wl.fit_weights(samples, ["model", "espn"])
    assert fitted["model"] > 0.9 and sum(fitted.values()) == pytest.approx(1.0, abs=0.001)


def test_blend_rebalances_over_the_sources_a_player_has():
    assert wl.blend({"model": 0.2, "espn": 0.6}, {"model": 10.0}) == 10.0
    assert wl.blend({"model": 0.25, "espn": 0.75}, {"model": 10.0, "espn": 20.0}) == 17.5


def test_conservative_step_is_capped_floored_and_scaled_by_evidence():
    current = dict(DEFAULT_WEIGHTS)
    extreme = {"vegas_props": 0.0, "josh_hayden": 0.0, "model": 1.0, "yahoo_consensus": 0.0, "espn": 0.0}
    small = wl.conservative_step(current, extreme, rows=300)
    assert all(abs(small[k] - current[k]) <= wl.MAX_STEP + 0.01 for k in current)
    assert min(small.values()) >= wl.MIN_WEIGHT - 0.001 and sum(small.values()) == pytest.approx(1.0, abs=0.001)
    tiny = wl.conservative_step(current, extreme, rows=10)
    assert abs(tiny["model"] - current["model"]) < abs(small["model"] - current["model"])
    many = current
    for _ in range(40):                                  # many weeks of the same evidence still respect the floor
        many = wl.conservative_step(many, extreme, rows=3000)
    assert many["model"] > 0.7 and min(many.values()) >= wl.MIN_WEIGHT - 0.001


def _seed_history(session, seeded, week, final=True, players=120):
    session.add(NflGame(game_id=f"lw{week}", season=2026, week=week, game_type="REG", home_team="AAA",
                        away_team="BBB", home_score=24 if final else None, away_score=20 if final else None))
    pids = [seeded[k] for k in seeded if isinstance(seeded[k], int)][:1]
    from database.models import Player

    pool = [p.id for p in session.query(Player).limit(players).all()]
    for i, pid in enumerate(pool):
        actual = 5.0 + i % 15
        session.add(ProjectionHistory(season=2026, week=week, scoring="ppr", player_id=pid, position="WR",
                                      projection=actual, if_active=actual, play_prob=1.0, floor=None, ceiling=None,
                                      sources=json.dumps({"model": actual + 0.5, "espn": actual + (5 if i % 2 else -5)})))
        session.add(PlayerStatsWeekly(player_id=pid, season=2026, week=week, season_type="REG",
                                      fantasy_points=actual - 1, receptions=1))
    session.commit()
    return pids


def test_learn_updates_once_per_completed_week_and_rankings_use_it(session, seeded):
    _seed_history(session, seeded, week=9)
    _seed_history(session, seeded, week=10, final=False)            # not finished: skipped
    updates = wl.learn(session, 2026)
    assert [u.week for u in updates] == [9]
    update = updates[0]
    assert update.after["model"] > DEFAULT_WEIGHTS["model"] and update.after["espn"] < DEFAULT_WEIGHTS["espn"]
    assert update.mae_after <= update.mae_before
    assert wl.learn(session, 2026) == []                             # already learned: no double counting
    weights, row = wl.effective_weights(session)
    assert row.week == 9 and weights == update.after


def test_dry_run_saves_nothing_and_defaults_apply_without_history(session, seeded):
    assert wl.effective_weights(session) == (dict(DEFAULT_WEIGHTS), None)
    _seed_history(session, seeded, week=9)
    assert wl.learn(session, 2026, apply=False)
    assert session.query(LearnedWeights).count() == 0


def test_too_few_rows_are_not_learned_from(session, seeded):
    _seed_history(session, seeded, week=9, players=20)
    assert wl.learn(session, 2026) == []


def test_past_weeks_use_only_weights_learned_before_them(session, seeded):
    _seed_history(session, seeded, week=9)
    wl.learn(session, 2026)
    assert wl.effective_weights(session, 2026, 9) == (dict(DEFAULT_WEIGHTS), None)     # week 9 itself: no hindsight
    weights, row = wl.effective_weights(session, 2026, 10)
    assert row.week == 9 and weights != DEFAULT_WEIGHTS
    assert wl.effective_weights(session, 2027, 1)[1].week == 9                         # carries into next season
