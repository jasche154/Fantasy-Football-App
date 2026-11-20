"""Replay League values: point-in-time knowledge, and that the app's lineup and trade engines run on it."""
import time

import numpy as np
import pytest

from services import lineup_gain, my_team, replay_state, replay_values as rv, trade
from services.league import LeagueSettings
from services.replay_pool import SKILL
from tests import replay_fakes as fakes

END = fakes.fake_settings().end_week


def know_at(week: int, data=None) -> rv.Knowledge:
    return fakes.fake_knowledge(week, data=data)


def by_adp(know: rv.Knowledge, position: str) -> list[rv.ReplayValue]:
    return sorted((v for v in know.values if v.position == position and v.adp is not None), key=lambda v: v.adp)


def roster_of(know: rv.Knowledge, *pos_ranks: str) -> list[rv.ReplayValue]:
    by_rank = {v.pos_rank: v for v in know.values}
    return [by_rank[rank] for rank in pos_ranks]


def middling_roster(know: rv.Knowledge) -> list[rv.ReplayValue]:
    return roster_of(know, "QB14", "RB26", "RB38", "RB50", "WR26", "WR38", "WR50", "TE14")


# ---------------- the shape of Knowledge ----------------

def test_knowledge_covers_the_whole_pool_ranked_best_first():
    data, know = fakes.fake_season(), know_at(5)

    assert len(know.values) == len(data.players) == len(know.by_id)
    assert [v.rank for v in know.values] == list(range(1, len(know.values) + 1))
    assert all(a.trade_value >= b.trade_value for a, b in zip(know.values, know.values[1:]))
    assert all(know.by_id[v.player_id] is v for v in know.values)
    assert sorted(v.pos_rank for v in know.values if v.position == "TE")[:2] == ["TE1", "TE10"]
    assert (know.season, know.week, know.through_week, know.weeks_remaining) == (fakes.SEASON, 5, 4, END - 4)
    assert know.schedule is data.schedule and set(know.replacement) == set(SKILL)


def test_trade_value_is_points_over_replacement_for_the_games_left():
    data, know = fakes.fake_season(), know_at(5)
    back = roster_of(know, "RB8")[0]

    games_left = len(data.schedule.remaining_opponents(back.team, 4, END))
    over = back.ros_ppg - know.replacement["RB"]

    assert back.availability == 1.0 and back.vorp_per_game == pytest.approx(over, abs=0.006)
    assert back.trade_value == pytest.approx(over * games_left, abs=0.06)
    assert back.healthy_value == back.trade_value and not back.out          # healthy: no discount
    assert back.games_left == games_left and games_left in (11, 12)


def test_kickers_and_defenses_have_no_trade_value_but_still_project():
    know = know_at(3)
    specials = [v for v in know.values if v.position in ("K", "DST")]
    kickers = sorted((v for v in specials if v.position == "K"), key=lambda v: int(v.pos_rank[1:]))

    assert len(specials) == 66
    assert all(v.trade_value == 0.0 and v.vorp_per_game == 0.0 and v.healthy_value == 0.0 for v in specials)
    assert sum(v.ros_ppg > 5 for v in specials) >= 60 and sum(v.projection > 0 for v in specials) >= 60
    assert all(a.ros_ppg >= b.ros_ppg for a, b in zip(kickers, kickers[1:]))       # ranked by rate within position


def test_knowledge_is_cached_and_fast():
    data, league = fakes.fake_season(), rv.league_settings(fakes.fake_settings())
    started = time.perf_counter()
    built = [rv.knowledge(data, league, week, END) for week in range(0, 17)]
    fresh = (time.perf_counter() - started) / len(built)
    started = time.perf_counter()
    again = [rv.knowledge(data, rv.league_settings(fakes.fake_settings()), week, END) for week in range(0, 17)]
    cached = (time.perf_counter() - started) / len(again)

    assert all(a is b for a, b in zip(built, again))
    assert cached < 0.05 and fresh < 0.25
    assert rv.knowledge(data, league, 5, END) is not rv.knowledge(data, league, 5, END - 1)


def test_knowledge_rejects_a_negative_week_and_data_that_stops_too_early():
    data, league = fakes.fake_season(), rv.league_settings(fakes.fake_settings())

    with pytest.raises(ValueError):
        rv.knowledge(data, league, -1, END)
    with pytest.raises(ValueError, match="stops before week 3"):
        rv.knowledge(data.as_of(3), league, 6, END)
    assert rv.knowledge(data.as_of(0), league, 1, END).values == rv.knowledge(data, league, 1, END).values


def test_league_settings_carry_the_replay_league_into_the_engine():
    settings = fakes.fake_settings(teams=10, scoring="ppr", bench=5, regular_season_weeks=13)

    league = rv.league_settings(settings)

    assert isinstance(league, LeagueSettings)
    assert (league.teams, league.scoring, league.bench, league.superflex) == (10, "ppr", 5, False)
    assert dict(league.starters) == dict(settings.starters) and league.starters["K"] == league.starters["DST"] == 1
    assert league.playoff_weeks == (14, 15)
    assert league == rv.league_settings(settings) and hash(league) == hash(rv.league_settings(settings))


# ---------------- priors and rates ----------------

@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE", "K", "DST"])
def test_draft_time_values_follow_adp_order_within_a_position(position):
    data, know = fakes.fake_season(), know_at(0)

    drafted = by_adp(know, position)
    best_week_one = max(data.projection(v.player_id, 1) for v in know.values if v.position == position)

    assert len(drafted) >= 12
    assert all(a.ros_ppg >= b.ros_ppg for a, b in zip(drafted, drafted[1:]))
    assert drafted[0].ros_ppg == pytest.approx(best_week_one, abs=0.006)        # the curve is week-1 projections


def test_the_draft_board_shows_the_adp_prior_not_the_week_one_projection():
    data, know = fakes.fake_season(), know_at(0)
    drafted = [v for v in know.values if v.adp is not None]
    undrafted = next(v for v in know.values if v.adp is None and data.projection(v.player_id, 1) > 0)

    assert all(v.projection == pytest.approx(v.ros_ppg, abs=0.006) for v in drafted)
    assert sum(v.projection != data.projection(v.player_id, 1) for v in drafted) > 150     # post-ADP news hidden
    assert know.by_id[fakes.NEVER_PLAYED_ID].projection > 5 and not know.by_id[fakes.NEVER_PLAYED_ID].out
    assert undrafted.projection == data.projection(undrafted.player_id, 1)
    assert know_at(1).by_id[drafted[0].player_id].projection == data.projection(drafted[0].player_id, 1)


@pytest.mark.parametrize("position", SKILL)
def test_week_one_values_still_track_adp_within_a_position(position):
    drafted = by_adp(know_at(1), position)

    rates = np.array([v.ros_ppg for v in drafted])
    order = np.argsort(np.argsort(-rates))
    agreement = np.corrcoef(order, np.arange(len(drafted)))[0, 1]

    assert agreement > 0.9


def test_an_undrafted_player_starts_from_his_own_week_one_projection():
    data, know = fakes.fake_season(), know_at(0)
    undrafted = next(v for v in know.values if v.adp is None and v.position == "WR")
    new_kicker = know.by_id[fakes.player_id("K", fakes.KICKER_CHANGES[0][1])]

    assert undrafted.ros_ppg == data.projection(undrafted.player_id, 1) > 0
    assert (new_kicker.ros_ppg, new_kicker.projection, new_kicker.availability) == (0.0, 0.0, 1.0)


def test_rest_of_season_rate_weights_the_last_four_projections_newest_first():
    data = fakes.fake_season()
    prior = know_at(0).by_id[fakes.BREAKOUT_ID].ros_ppg
    seen = {w: data.projection(fakes.BREAKOUT_ID, w) for w in range(1, 10) if data.projection(fakes.BREAKOUT_ID, w)}
    newest = sorted(seen, reverse=True)[:4]

    week_one, week_two, week_nine = (know_at(w).by_id[fakes.BREAKOUT_ID] for w in (1, 2, 9))

    assert rv.RECENCY_WEIGHTS == (4, 3, 2, 1)
    assert week_one.ros_ppg == pytest.approx((4 * seen[1] + 6 * prior) / 10, abs=0.006)
    assert week_two.ros_ppg == pytest.approx((4 * seen[2] + 3 * seen[1] + 3 * prior) / 10, abs=0.006)
    assert week_nine.ros_ppg == pytest.approx(sum(wt * seen[w] for wt, w in zip((4, 3, 2, 1), newest)) / 10, abs=0.006)
    assert week_nine.projection == week_nine.last_projection == seen[9]


def test_a_new_player_without_a_prior_earns_his_rate_over_four_projections():
    backup, first_week = fakes.player_id("K", fakes.KICKER_CHANGES[0][1]), fakes.KICKER_CHANGES[0][2]
    data = fakes.fake_season()

    first = know_at(first_week).by_id[backup]

    assert first.ros_ppg == pytest.approx(0.4 * data.projection(backup, first_week), abs=0.006)
    assert not first.out and first.last_projection == data.projection(backup, first_week)


def test_a_breakout_rises_and_a_bust_falls_as_projections_catch_up():
    early, late = know_at(1), know_at(10)

    assert early.by_id[fakes.BREAKOUT_ID].rank > 100 and late.by_id[fakes.BREAKOUT_ID].rank < 25
    assert early.by_id[fakes.BUST_ID].rank < 15 and late.by_id[fakes.BUST_ID].rank > 80


def test_games_and_points_per_game_count_only_weeks_already_played():
    data = fakes.fake_season()
    played = [data.actual[(fakes.BREAKOUT_ID, w)] for w in (1, 2, 3)]

    before, later = (know_at(w).by_id[fakes.BREAKOUT_ID] for w in (1, 4))

    assert (before.games, before.ppg) == (0, 0.0)
    assert later.games == 3 and later.ppg == pytest.approx(sum(played) / 3, abs=0.006)


def test_team_is_the_one_known_before_the_week():
    data = fakes.fake_season()
    old, new = (dict(data.team_by_week[fakes.TRADED_ID])[w] for w in (fakes.TRADED_WEEK - 1, fakes.TRADED_WEEK))

    assert know_at(fakes.TRADED_WEEK).by_id[fakes.TRADED_ID].team == old
    assert know_at(fakes.TRADED_WEEK + 1).by_id[fakes.TRADED_ID].team == new


# ---------------- injuries and byes ----------------

def test_a_star_with_no_projection_loses_a_share_of_his_value_and_gets_it_all_back():
    weeks = {w: know_at(w).by_id[fakes.INJURED_STAR_ID] for w in range(5, 11)}

    assert [weeks[w].out for w in range(5, 11)] == [False, True, True, True, True, False]
    assert [weeks[w].availability for w in range(5, 11)] == [1.0, 0.55, 0.45, 0.4, 0.35, 1.0]
    assert all(weeks[w].expected_missed == pytest.approx(weeks[w].games_left * (1 - weeks[w].availability), abs=0.006)
               for w in range(5, 11))
    assert all(weeks[w].trade_value == pytest.approx(weeks[w].healthy_value * weeks[w].availability, abs=0.06)
               for w in range(5, 11))
    assert weeks[5].trade_value > weeks[6].trade_value > weeks[7].trade_value > weeks[8].trade_value
    assert weeks[9].trade_value > 25 and weeks[9].rank < 100            # four weeks out: no cliff, still a top asset
    assert weeks[10].trade_value == weeks[10].healthy_value > 50        # projected again: full value at once
    assert all(weeks[w].projection == 0.0 for w in fakes.INJURED_WEEKS)


def test_a_long_absence_never_takes_trade_value_to_zero():
    data = fakes.fake_season()
    gone = fakes.patched(data, projected={(fakes.INJURED_STAR_ID, w): None for w in range(10, 17)})

    shares = {w: know_at(w, gone).by_id[fakes.INJURED_STAR_ID] for w in (10, 11, 13, 16)}

    assert [shares[w].availability for w in (10, 11, 13, 16)] == [0.25, 0.1, 0.1, 0.1]     # week 12 is his bye
    assert rv.OUT_SHARE == {1: 0.45, 2: 0.55, 3: 0.60, 4: 0.65, 5: 0.75} and rv.OUT_SHARE_LONG == 0.90
    assert all(v.out and v.trade_value > 0 and v.expected_missed < v.games_left for v in shares.values())
    assert shares[16].games_left == 1 and shares[16].healthy_value > 10


def test_an_absence_keeps_the_rate_he_had_instead_of_falling_back_to_adp():
    data = fakes.fake_season()
    healthy, out, back = (know_at(w).by_id[fakes.INJURED_STAR_ID] for w in (5, 9, 10))
    seen = [data.projection(fakes.INJURED_STAR_ID, w) for w in (10, 5, 4, 3)]       # his last four, newest first

    assert out.ros_ppg == healthy.ros_ppg != know_at(0).by_id[fakes.INJURED_STAR_ID].ros_ppg
    assert out.last_projection == data.projection(fakes.INJURED_STAR_ID, 5) and out.projection == 0.0
    assert back.ros_ppg == pytest.approx(sum(wt * points for wt, points in zip((4, 3, 2, 1), seen)) / 10, abs=0.006)


def test_a_drafted_player_who_never_plays_is_out_from_week_one_and_fades():
    values = {w: know_at(w).by_id[fakes.NEVER_PLAYED_ID] for w in (0, 1, 3, 6, 16)}

    assert values[0].availability == 1.0 and not values[0].out          # draft day: nobody knows yet
    assert [values[w].availability for w in (1, 3, 6, 16)] == [0.55, 0.4, 0.1, 0.1]
    assert all(values[w].out and values[w].team is None and values[w].projection == 0.0 for w in (1, 3, 6, 16))
    assert values[0].trade_value > values[1].trade_value > values[3].trade_value > values[6].trade_value > 0
    assert len({values[w].ros_ppg for w in values}) == 1                # only ever his ADP prior
    assert all(rv.projector(know_at(6)).points(values[6], w) == 0.0 for w in range(6, 17))


def test_a_bye_week_is_not_an_absence():
    data = fakes.fake_season()
    bye = next(w for w in fakes.BYE_WEEKS if data.on_bye(fakes.BUST_ID, w))

    on_bye = know_at(bye).by_id[fakes.BUST_ID]

    assert (on_bye.projection, on_bye.expected_missed, on_bye.availability, on_bye.out) == (0.0, 0.0, 1.0, False)
    assert on_bye.trade_value > 0


@pytest.mark.parametrize("player", [fakes.BUST_ID, fakes.BREAKOUT_ID, fakes.INJURED_STAR_ID])
def test_a_bye_does_not_change_the_rest_of_season_rate(player):
    data = fakes.fake_season()
    bye = next(w for w in fakes.BYE_WEEKS if data.on_bye(player, w))

    before, during = know_at(bye - 1).by_id[player], know_at(bye).by_id[player]

    assert data.projection(player, bye - 1) > 0 and data.projection(player, bye) == 0.0
    assert during.ros_ppg == before.ros_ppg and during.last_projection == before.projection
    assert during.vorp_per_game == pytest.approx(before.vorp_per_game, abs=0.5)     # only replacement moved


def test_a_bye_inside_an_absence_does_not_lengthen_it():
    data = fakes.fake_season()
    bye = next(w for w in fakes.BYE_WEEKS if data.on_bye(fakes.BUST_ID, w))
    hurt = fakes.patched(data, projected={(fakes.BUST_ID, bye - 1): None, (fakes.BUST_ID, bye + 1): None})

    value = know_at(bye + 1, hurt).by_id[fakes.BUST_ID]

    assert data.projection(fakes.BUST_ID, bye - 2) > 0
    assert value.availability == 0.45 and value.out                     # out two games, not three weeks
    assert know_at(bye, hurt).by_id[fakes.BUST_ID].availability == 0.55


def test_a_replaced_kicker_becomes_unavailable_and_his_replacement_appears():
    starter, backup, first_week = fakes.KICKER_CHANGES[0]
    old_id, new_id = fakes.player_id("K", starter), fakes.player_id("K", backup)

    before, after = know_at(first_week - 1), know_at(first_week + 4)

    assert before.by_id[old_id].availability == 1.0 and before.by_id[new_id].ros_ppg == 0.0
    assert before.by_id[new_id].expected_missed == 0.0                  # never projected: nobody is waiting on him
    assert after.by_id[old_id].out and 0 < after.by_id[old_id].availability <= 0.35
    assert after.by_id[old_id].projection == 0.0
    assert after.by_id[new_id].ros_ppg > 4.5 and after.by_id[new_id].projection > 0     # four projections in


# ---------------- no hindsight ----------------

@pytest.mark.parametrize("week", [0, 1, 2, 5, 9, 13, 16])
def test_nothing_known_before_a_week_depends_on_later_weeks(week):
    data = fakes.fake_season()
    other = fakes.future_changed(data, after_week=max(1, week))        # draft time may use week-1 projections

    real, altered = know_at(week, data), know_at(week, other)

    assert other is not data and dict(other.actual) != dict(data.actual)
    assert real.values == altered.values
    assert dict(real.replacement) == dict(altered.replacement)
    assert (real.weeks_remaining, real.through_week) == (altered.weeks_remaining, altered.through_week)


@pytest.mark.parametrize("week", [0, 1, 6, 12])
def test_knowledge_is_the_same_for_the_full_season_and_its_as_of_copy(week):
    data, league = fakes.fake_season(), rv.league_settings(fakes.fake_settings())
    other = fakes.future_changed(data, after_week=max(1, week))

    full, limited = rv.knowledge(data, league, week, END), rv.knowledge(data.as_of(week), league, week, END)

    assert limited is full                                              # knowledge only ever reads as_of(week)
    assert rv.knowledge(other.as_of(week), league, week, END).values == full.values
    assert rv.knowledge(data.as_of(week + 3), league, week, END).values == full.values


def test_real_results_never_move_values_only_the_display_fields():
    data = fakes.fake_season()
    boosted = fakes.patched(data, actual={key: points + 30 for key, points in data.actual.items()})

    real, altered = know_at(8, data), know_at(8, boosted)

    assert [(v.player_id, v.ros_ppg, v.trade_value, v.rank) for v in real.values] == [
        (v.player_id, v.ros_ppg, v.trade_value, v.rank) for v in altered.values]
    assert altered.by_id[fakes.BUST_ID].ppg == pytest.approx(real.by_id[fakes.BUST_ID].ppg + 30, abs=0.01)


def test_this_weeks_projection_does_move_values():
    data = fakes.fake_season()
    hyped = fakes.patched(data, projected={(fakes.BUST_ID, 8): 40.0})

    assert know_at(8, hyped).by_id[fakes.BUST_ID].ros_ppg > know_at(8, data).by_id[fakes.BUST_ID].ros_ppg + 5
    assert know_at(7, hyped).by_id[fakes.BUST_ID] == know_at(7, data).by_id[fakes.BUST_ID]


# ---------------- the lineup engine runs on it ----------------

def test_projector_and_horizon_are_the_engines_own_types():
    know, settings = know_at(6), fakes.fake_settings()

    projector, horizon = rv.projector(know), rv.horizon(know, settings)

    assert isinstance(projector, lineup_gain.Projector) and isinstance(horizon, lineup_gain.Horizon)
    assert projector.current_week == 6 and horizon.current_week == 6
    assert horizon.weeks == list(range(6, 17))
    assert horizon.weights[6] == 1.0 and horizon.weights[15] == horizon.weights[16] == lineup_gain.DEFAULT_PLAYOFF_ODDS
    assert rv.projector(know_at(0)).current_week == 1 and rv.horizon(know_at(0), settings).weeks[0] == 1


def test_one_projector_is_shared_per_knowledge():
    know = know_at(6)
    back = roster_of(know, "RB8")[0]

    first, again = rv.projector(know), rv.projector(know)
    first.points(back, 9)

    assert again is first and (back.player_id, 9) in again._cache        # every team reuses the point cache
    assert rv.projector(know_at(7)) is not first
    rv.clear_cache()
    assert rv.projector(know) is not first


def test_week_lineup_starts_the_best_skill_players_at_this_weeks_projection():
    know = know_at(6)
    roster = middling_roster(know) + roster_of(know, "K5", "DST5")

    points, starters = lineup_gain.week_lineup(roster, 6, rv.projector(know), know.league)

    assert len(starters) == 7 and all(know.by_id[pid].position in SKILL for pid in starters)
    assert points == pytest.approx(sum(know.by_id[pid].projection for pid in starters))
    assert points > 40


def test_a_later_week_uses_the_healthy_rate_and_a_bye_scores_zero():
    data, know = fakes.fake_season(), know_at(3)
    projector = rv.projector(know)
    back = roster_of(know, "RB8")[0]
    bye = next(w for w in fakes.BYE_WEEKS if data.on_bye(back.player_id, w))

    assert projector.points(back, 3) == back.projection
    assert projector.points(back, bye) == 0.0
    assert projector.points(back, 14) == pytest.approx(back.ros_ppg * lineup_gain.healthy_share("RB"))


def test_gain_and_best_add_value_a_star_for_a_middling_roster():
    know, settings = know_at(6), fakes.fake_settings()
    projector, horizon = rv.projector(know), rv.horizon(know, settings)
    roster = middling_roster(know)
    star, droppable = roster_of(know, "RB2")[0], roster_of(know, "RB50", "WR50")

    plain = lineup_gain.gain(roster, horizon, projector, know.league, add=[star], drop=droppable[:1])
    with_drop, dropped = lineup_gain.best_add(roster, droppable, star, horizon, projector, know.league)
    open_spot, nobody = lineup_gain.best_add(roster, [], star, horizon, projector, know.league)
    pointless, _ = lineup_gain.best_add(roster, droppable, roster_of(know, "RB80")[0], horizon, projector, know.league)

    assert plain > 20 and with_drop >= plain and dropped in droppable
    assert open_spot >= with_drop and nobody is None
    assert pointless <= 0.5
    assert lineup_gain.handcuffs(roster, horizon, projector, know.league) <= {v.player_id for v in roster}


def test_the_replay_projector_sits_an_injured_player_out_and_never_inflates_his_rate():
    know = know_at(8)                                     # three weeks out so far
    star = know.by_id[fakes.INJURED_STAR_ID]
    projector, engine_default = rv.projector(know), lineup_gain.Projector(know, {}, {}, 8)
    weekly = [projector.points(star, week) for week in range(8, END + 1)]

    assert star.availability == 0.4 and star.expected_missed == pytest.approx(0.6 * star.games_left)
    assert weekly[:4] == [0.0] * 4                        # sat out for the games he is expected to miss
    assert 0 < weekly[-1] <= star.ros_ppg and max(weekly) <= star.ros_ppg
    assert engine_default.points(star, 15) > star.ros_ppg       # PlayerValue's rate is pre-discounted; ours is not


@pytest.mark.parametrize("week", [2, 6, 9, 13])
def test_no_out_player_ever_projects_above_his_healthy_rate(week):
    know = know_at(week)
    projector = rv.projector(know)
    out = [v for v in know.values if v.expected_missed > 0]

    assert len(out) >= 5 and all(v.out and v.availability < 1 for v in out)
    assert all(projector.points(v, w) <= v.ros_ppg for v in out for w in range(week, END + 1))
    assert all(projector.points(v, week) == 0.0 for v in out)


# ---------------- the trade engine runs on it ----------------

def test_trade_packages_discount_depth_and_ignore_kickers():
    know = know_at(4)
    first, second, kicker = roster_of(know, "RB5", "WR20", "K1")

    side = trade.package([second, first, kicker])

    assert [p.value for p in side.players] == [first, second, kicker]
    assert side.raw_total == pytest.approx(first.trade_value + second.trade_value, abs=0.11)
    assert side.adjusted_total == pytest.approx(first.trade_value + 0.7 * second.trade_value, abs=0.11)
    assert trade.package([kicker]).adjusted_total == 0.0


def test_evaluate_trade_judges_replay_values():
    know = know_at(4)
    star, starter, similar = roster_of(know, "RB2", "RB30", "RB3")

    lopsided = trade.evaluate_trade([star], [starter], know.values, know.weeks_remaining)
    even = trade.evaluate_trade([star], [similar], know.values, know.weeks_remaining)

    assert lopsided.verdict == "big_loss" and lopsided.difference < 0
    assert lopsided.give.raw_total == star.trade_value
    assert all(s.add_to == "get" and s.value.trade_value > 0 for s in lopsided.balance_suggestions)
    assert even.verdict in ("fair", "slight_loss", "slight_win")
    with pytest.raises(ValueError):
        trade.evaluate_trade([star], [star], know.values, know.weeks_remaining)


# ---------------- what a player counts for in a trade package ----------------

def test_market_value_is_the_trade_value_for_a_starter_and_a_floor_for_the_rest():
    know = know_at(6)
    star, scrub = roster_of(know, "RB3", "RB80")
    out = know.by_id[fakes.INJURED_STAR_ID]

    assert rv.market_value(know, star.player_id) == star.market_value == star.trade_value > 100
    assert scrub.trade_value == 0.0
    assert rv.market_value(know, scrub.player_id) == pytest.approx(trade.FAIR_POINTS_PER_WEEK * scrub.games_left)
    assert rv.market_value(know, out.player_id) == out.trade_value < out.healthy_value      # discounted, not floored
    assert all(v.market_value >= round(trade.FAIR_POINTS_PER_WEEK * v.games_left, 1) > 0 for v in know.values)
    with pytest.raises(ValueError):
        rv.market_value(know, 999_999)


@pytest.mark.parametrize("position", ["K", "DST"])
def test_kickers_and_defenses_are_worth_little_but_never_nothing(position):
    know, teams = know_at(6), fakes.fake_settings().teams
    ranked = sorted((v for v in know.values if v.position == position), key=lambda v: -v.ros_ppg)
    best, streamer = ranked[0], ranked[teams]
    floors = {v.player_id: round(trade.FAIR_POINTS_PER_WEEK * v.games_left, 1) for v in ranked}
    healthy = [v for v in ranked if not v.out]

    assert best.market_value == pytest.approx(
        floors[best.player_id] + (best.ros_ppg - streamer.ros_ppg) * best.games_left, abs=0.06)
    assert floors[best.player_id] < best.market_value < 40              # a fraction of a skill starter
    assert all(v.market_value == floors[v.player_id] for v in ranked[teams:])       # waiver level: the floor
    assert all(a.market_value >= b.market_value - 1.0 for a, b in zip(healthy, healthy[1:]))    # byes differ by 0.4
    assert best.market_value < roster_of(know, "RB24")[0].market_value


def test_market_floor_shrinks_with_the_games_left():
    scrub = roster_of(know_at(6), "RB80")[0].player_id

    early, late = know_at(2).by_id[scrub], know_at(16).by_id[scrub]

    assert early.market_value > late.market_value == pytest.approx(trade.FAIR_POINTS_PER_WEEK * late.games_left)
    assert late.games_left == 1


# ---------------- lineups over all six positions ----------------

LEGAL = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DST"]


@pytest.mark.parametrize("positions, holes", [
    ([*LEGAL, "WR"], 0), ([*LEGAL, "RB"], 0), ([*LEGAL, "TE"], 0),     # any RB/WR/TE fills the FLEX
    ([*LEGAL, "QB"], 1), (LEGAL, 1),                                    # a second QB does not
    ([*LEGAL[:-1], "WR"], 1), ([*LEGAL[:-2], "WR", "DST"], 1),          # no DST / no K
    (["QB", "RB", "WR", "WR", "WR", "TE", "K", "DST"], 1),              # the spare WR is the FLEX; an RB is missing
    (["RB"] * 9, 6), ([], 9), ([*LEGAL, "WR", "K", "DST", "QB", "TE", "RB"], 0),
])
def test_lineup_holes_counts_the_starting_slots_a_roster_cannot_fill(positions, holes):
    settings = fakes.fake_settings()

    assert rv.lineup_holes(positions, settings) == holes
    assert rv.can_field_lineup(positions, settings) is (holes == 0)
    assert rv.lineup_holes(positions, rv.league_settings(settings)) == holes        # LeagueSettings works too


def test_can_field_lineup_follows_the_leagues_own_slots():
    two_flex = replay_state.Settings(season=fakes.SEASON, starters={"QB": 1, "RB": 1, "WR": 1, "FLEX": 2})

    assert rv.can_field_lineup(["QB", "RB", "WR", "TE", "TE"], two_flex)            # TE has no slot: both flex
    assert rv.lineup_holes(["QB", "RB", "WR", "TE"], two_flex) == 1
    assert rv.lineup_holes(["QB", "RB", "RB", "RB"], two_flex) == 1                 # no WR
    assert rv.can_field_lineup([], replay_state.Settings(season=fakes.SEASON, starters={}))


def test_can_field_lineup_agrees_with_the_lineup_builder_on_real_rosters():
    know, settings = know_at(1), fakes.fake_settings()
    slots = sum(settings.starters.values())

    for team in fakes.drafted_state(settings).teams:
        for size in (len(team.roster), 9, 6):
            ids = team.roster[-size:]
            filled = sum(len(players) for players in rv.best_lineup(know, ids, settings).values())
            assert rv.can_field_lineup([know.by_id[pid].position for pid in ids], settings) is (filled == slots)


def test_best_lineup_fills_kicker_and_defense_through_the_apps_lineup_builder():
    know, settings = know_at(6), fakes.fake_settings()
    roster = middling_roster(know) + roster_of(know, "K5", "K25", "DST5", "DST25")
    ids = [v.player_id for v in roster]
    better = {pos: max((v for v in roster if v.position == pos), key=lambda v: v.projection) for pos in ("K", "DST")}

    lineup = rv.best_lineup(know, ids, settings)

    assert {slot: len(players) for slot, players in lineup.items()} == dict(settings.starters)
    assert lineup["K"] == [better["K"].player_id] and lineup["DST"] == [better["DST"].player_id]
    assert know.by_id[lineup["FLEX"][0]].position in ("RB", "WR", "TE")
    starters = [pid for players in lineup.values() for pid in players]
    assert len(set(starters)) == 9 and set(starters) <= set(ids)
    assert lineup == my_team.best_lineup(roster, know.league)            # the same builder, the same answer
    assert rv.best_lineup(know, ids, know.league) == lineup              # LeagueSettings works too
    assert rv.lineup_points(know, starters) == pytest.approx(sum(know.by_id[pid].projection for pid in starters))


def test_best_lineup_breaks_projection_ties_with_the_rest_of_season_rate():
    data = fakes.fake_season()
    one, two = fakes.player_id("K", 3), fakes.player_id("K", 20)
    level = know_at(3, fakes.patched(data, projected={(one, 3): 7.0, (two, 3): 7.0}))
    better, worse = sorted((level.by_id[one], level.by_id[two]), key=lambda v: -v.ros_ppg)

    lineup = rv.best_lineup(level, [worse.player_id, better.player_id], fakes.fake_settings())

    assert better.projection == worse.projection == 7.0 and better.ros_ppg > worse.ros_ppg
    assert lineup == {"K": [better.player_id]}


def test_lineup_helpers_reject_unknown_players():
    know = know_at(2)

    with pytest.raises(ValueError):
        rv.best_lineup(know, [fakes.BUST_ID, 999_999], fakes.fake_settings())
    with pytest.raises(ValueError):
        rv.lineup_points(know, [999_999])
    assert rv.best_lineup(know, [], fakes.fake_settings()) == {} and rv.lineup_points(know, []) == 0.0
