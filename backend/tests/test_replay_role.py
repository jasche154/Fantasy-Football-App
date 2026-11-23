"""Replay League chart model extras (services/replay_role.py): the newest projection and vacated work."""
import pytest

from services import replay_role as role, replay_values as rv
from services.replay_pool import Injury
from tests import replay_fakes as fakes

END = fakes.fake_settings().end_week
LEAD, BACKUP = fakes.player_id("RB", 2), fakes.player_id("RB", 34)       # both DAL backs in the fake season
GONE, SECOND, THIRD, ELSEWHERE = 90_001, 90_002, 90_003, 90_004          # not in the pool: never projected


def _row(pid: int, team: str | None, rate: float, availability: float = 1.0, position: str = "WR",
         last_projection: float = 0.0) -> dict:
    return {"player_id": pid, "position": position, "team": team, "ros_ppg": rate,
            "last_projection": last_projection, "availability": availability}


def _rates(group: list[dict]) -> dict[int, float]:
    return {r["player_id"]: r["ros_ppg"] for r in group}


def test_the_newest_projection_moves_the_rate_by_half_its_gap():
    group = [_row(LEAD, "DAL", 10.0, position="RB", last_projection=14.0),
             _row(BACKUP, "DAL", 6.0, position="RB")]                    # no projection yet: unchanged

    scores = role.model_scores(fakes.fake_season(), group, {LEAD: 10.5, BACKUP: 6.0}, 5)

    assert scores == {LEAD: pytest.approx(10.5 + role.LATEST_WEIGHT * 4.0), BACKUP: 6.0}


def test_a_teammate_out_for_good_leaves_his_work_to_the_others_at_his_position_by_rate():
    data = fakes.fake_season().as_of(8)
    group = [_row(GONE, "AAA", 18.0, availability=0.0), _row(SECOND, "AAA", 12.0),
             _row(THIRD, "AAA", 4.0, availability=0.5), _row(ELSEWHERE, "BBB", 15.0)]

    boost = role.vacated(data, group, _rates(group), 8)

    assert boost[THIRD] == pytest.approx(0.6 * 18 * 4 / 16)                     # out himself: still gains
    assert boost[SECOND] == pytest.approx(0.6 * 18 * 12 / 16 + 0.6 * 0.5 * 4 * 0.9)   # capped share of the third's
    assert ELSEWHERE not in boost and GONE not in boost                        # other team; out for good


def test_vacated_work_goes_only_to_a_receiver_no_projection_has_priced_it_for():
    data = fakes.patched(fakes.fake_season(), projected={
        (GONE, 4): 15.0, (GONE, 5): 15.0,                                    # absent from week 6
        **{(SECOND, w): 10.0 for w in range(4, 10)},                         # projected since: the site saw it
        **{(THIRD, w): 10.0 for w in range(2, 6)}})                          # out since week 6 himself
    group = [_row(GONE, "AAA", 15.0, availability=0.2), _row(SECOND, "AAA", 10.0), _row(THIRD, "AAA", 10.0)]

    boost = role.vacated(data, group, _rates(group), 9)

    assert SECOND not in boost
    assert boost[THIRD] == pytest.approx(0.6 * 0.8 * 15 * 0.5)


def test_quarterbacks_and_draft_time_get_no_vacated_work():
    data = fakes.fake_season()
    qbs = [_row(GONE, "AAA", 20.0, availability=0.0, position="QB"), _row(SECOND, "AAA", 8.0, position="QB")]
    wrs = [_row(GONE, "AAA", 18.0, availability=0.0), _row(SECOND, "AAA", 12.0)]

    assert role.vacated(data, qbs, _rates(qbs), 8) == {}
    assert role.vacated(data.as_of(0), wrs, _rates(wrs), 0) == {}


def _value(data, pid: int, week: int) -> rv.ReplayValue:
    return rv.knowledge(data, rv.league_settings(fakes.fake_settings()), week, END).by_id[pid]


def test_a_teammate_lost_for_the_season_lifts_a_back_who_is_out_himself():
    data = fakes.fake_season()
    lost = range(8, data.last_week + 1)
    hurt = fakes.tagged(fakes.patched(data, projected={(BACKUP, 8): None}), {(BACKUP, 8): Injury("O")})
    both = fakes.tagged(fakes.patched(hurt, projected={(LEAD, w): None for w in lost}),
                        {(LEAD, w): Injury("IR") for w in lost})

    before, after = _value(hurt, BACKUP, 8), _value(both, BACKUP, 8)

    assert after.out and after.chart > before.chart and after.healthy_chart > before.healthy_chart
    assert after.ros_ppg == before.ros_ppg              # the chart moves; waivers and lineups keep ros_ppg


def test_a_jump_in_the_newest_projection_lifts_the_chart_more_than_before(monkeypatch):
    data = fakes.fake_season()
    jumped = fakes.patched(data, projected={(BACKUP, 5): 3 * data.projection(BACKUP, 5) + 6})
    gain = _value(jumped, BACKUP, 5).chart - _value(data, BACKUP, 5).chart

    monkeypatch.setattr(role, "LATEST_WEIGHT", 0.0)
    rv.clear_cache()
    old_gain = _value(jumped, BACKUP, 5).chart - _value(data, BACKUP, 5).chart

    assert gain > old_gain > 0


@pytest.mark.parametrize("week", [2, 5, 9])
def test_model_scores_never_read_later_weeks(week):
    data = fakes.fake_season()
    other = fakes.future_changed(data, after_week=week)
    group = [_row(LEAD, "DAL", 15.0, 0.0, "RB"), _row(BACKUP, "DAL", 6.0, position="RB", last_projection=9.0),
             _row(fakes.player_id("RB", 66), "DAL", 3.0, position="RB")]
    base = _rates(group)

    scores = role.model_scores(data, group, base, week)

    assert scores == role.model_scores(other, group, base, week) == role.model_scores(data.as_of(week), group, base, week)
    assert scores != base                               # the probe sees something: LEAD is out, BACKUP projected
