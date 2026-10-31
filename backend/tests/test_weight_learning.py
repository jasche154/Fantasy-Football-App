"""Pooled, shrunk learning of the Week X source weights from actual results."""
import json

import pytest

from database.models import LearnedWeights, NflGame, PlayerStatsWeekly, ProjectionHistory
from services import weight_learning as wl
from services.weight_learning import DEFAULT_WEIGHTS, Sample


def test_experts_are_one_group_and_shares_split_a_groups_weight():
    assert wl.grouped({"josh_hayden": 10.0, "yahoo_consensus": 14.0, "model": 9.0}) == {"experts": 12.0, "model": 9.0}
    shares = wl.source_shares(["josh_hayden", "yahoo_consensus", "model"], {"experts": 0.6, "model": 0.4,
                                                                           "vegas_props": 0.3, "espn": 0.1})
    assert shares == pytest.approx({"josh_hayden": 0.3, "yahoo_consensus": 0.3, "model": 0.4})
    assert wl.blend({"model": 0.25, "espn": 0.75}, {"model": 10.0, "espn": 20.0}) == 17.5
    assert wl.blend({"model": 0.2, "espn": 0.6}, {"model": 10.0}) == 10.0


def test_limits_keep_props_and_every_group_in_the_blend():
    out = wl.constrain({"vegas_props": 0.0, "experts": 0.9, "espn": -0.2, "model": 0.3})
    assert out["vegas_props"] == wl.PROPS_FLOOR and out["espn"] == wl.MIN_WEIGHT
    assert sum(out.values()) == pytest.approx(1.0, abs=0.001) and min(out.values()) >= 0


def test_pooled_fit_moves_toward_the_accurate_source_but_stays_near_the_prior(monkeypatch):
    monkeypatch.setattr(wl, "MIN_POOLED_ROWS", 10)
    samples = [Sample({"model": a, "espn": a + (6 if i % 2 else -6), "experts": a + 3, "vegas_props": a - 2}, a)
               for i, a in enumerate([5.0 + i % 20 for i in range(400)])]
    weak = wl.fit_pooled(samples)
    strong = wl.fit_pooled(samples, pseudo_rows=1)
    assert strong["model"] > weak["model"] > DEFAULT_WEIGHTS["model"]        # more evidence, bigger move
    assert abs(weak["experts"] - DEFAULT_WEIGHTS["experts"]) < 0.15            # the prior still anchors it
    monkeypatch.setattr(wl, "MIN_POOLED_ROWS", 1000)
    assert wl.fit_pooled(samples) == DEFAULT_WEIGHTS                            # too few rows: the prior


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


def test_only_fantasy_relevant_players_are_learned_from(session, seeded):
    _seed_history(session, seeded, week=9)
    samples = wl.samples_for_week(session, 2026, 9)
    assert len(samples) == wl.RELEVANT_RANKS["WR"] and all(s.week == 9 for s in samples)
    assert len(wl.samples_for_week(session, 2026, 9, relevant_only=False)) > len(samples)


def test_learn_refits_after_each_completed_week_and_rankings_use_it(session, seeded, monkeypatch):
    monkeypatch.setattr(wl, "MIN_POOLED_ROWS", 30)
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


def test_too_few_rows_keep_the_prior(session, seeded):
    _seed_history(session, seeded, week=9, players=20)
    (update,) = wl.learn(session, 2026)
    assert update.after == DEFAULT_WEIGHTS


def test_past_weeks_use_only_weights_learned_before_them(session, seeded, monkeypatch):
    monkeypatch.setattr(wl, "MIN_POOLED_ROWS", 30)
    _seed_history(session, seeded, week=9)
    wl.learn(session, 2026)
    assert wl.effective_weights(session, 2026, 9) == (dict(DEFAULT_WEIGHTS), None)     # week 9 itself: no hindsight
    weights, row = wl.effective_weights(session, 2026, 10)
    assert row.week == 9 and weights != DEFAULT_WEIGHTS
    assert wl.effective_weights(session, 2027, 1)[1].week == 9                         # carries into next season
    assert wl.position_weights(session, 2026, 10) == {}


def test_weights_learned_under_the_old_five_source_blend_are_ignored(session):
    session.add(LearnedWeights(season=2026, week=3, rows=100, optimal="{}", mae_before=1, mae_after=1,
                               weights=json.dumps({"vegas_props": 0.3, "josh_hayden": 0.3, "model": 0.2,
                                                   "yahoo_consensus": 0.1, "espn": 0.1})))
    session.commit()
    assert wl.effective_weights(session) == (dict(DEFAULT_WEIGHTS), None)
