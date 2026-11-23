"""Replay League opportunity shocks (services/replay_shocks.py): the market's reaction to a change of role."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_shocks as rs, replay_values as rv, replay_views
from services.replay_pool import Injury, Usage
from tests import replay_fakes as fakes

END = fakes.fake_settings().end_week
LEAD, BACKUP, THIRD = fakes.player_id("RB", 2), fakes.player_id("RB", 34), fakes.player_id("RB", 66)   # DAL backs
HURT = 8


def _lead_out(data, weeks):
    hurt = fakes.patched(data, projected={(LEAD, w): None for w in weeks})
    return fakes.tagged(hurt, {(LEAD, w): Injury("IR") for w in weeks})


def _mine(data, week, pid=BACKUP):
    return [s for s in rs.events(data, week) if s.player_id == pid]


def _value(data, pid, week):
    return rv.knowledge(data, rv.league_settings(fakes.fake_settings()), week, END).by_id[pid]


@pytest.fixture
def season_out():
    """The DAL lead back on IR from week HURT to the end; the data never says when (or if) he returns."""
    return _lead_out(fakes.fake_season(), range(HURT, fakes.fake_season().last_week + 1))


def test_a_lead_back_lost_lifts_the_next_back_in_line_at_once(season_out):
    shock, = _mine(season_out, HURT)
    before, after = _value(fakes.fake_season(), BACKUP, HURT), _value(season_out, BACKUP, HURT)

    assert (shock.kind, shock.other, shock.up, shock.week) == ("teammate_out", LEAD, True, HURT)
    assert shock.toward == rs.market_ranks(season_out.as_of(HURT), HURT)[LEAD]       # the market's rank of the lost lead
    assert not _mine(season_out, HURT, THIRD)                                         # only the next in line
    assert after.shock == "teammate_out" and after.shock_chart > 0
    assert after.chart > before.chart and after.ros_ppg == before.ros_ppg             # waivers and lineups keep ros_ppg


def test_the_move_fades_by_week_and_ends_after_fade_weeks(season_out):
    healthy, rank = {BACKUP: 300.0}, {BACKUP: 30.0}

    def delta(week):
        found = rs.moves(season_out, week, {LEAD: 0.0}, healthy, rank, lambda position, r: 1000.0 / r)
        return found[BACKUP][0] if BACKUP in found else 0.0

    first = delta(HURT)
    assert [delta(HURT + k) for k in range(rs.FADE_WEEKS + 1)] == pytest.approx(
        [first * (1 - k / rs.FADE_WEEKS) for k in range(rs.FADE_WEEKS)] + [0.0])
    assert first > 0


def test_nothing_moves_once_the_chart_has_caught_up_or_the_absence_is_short(season_out):
    shock, = _mine(season_out, HURT)
    value = lambda position, r: 1000.0 / r                                              # noqa: E731

    assert rs.moves(season_out, HURT, {LEAD: 0.0}, {BACKUP: 900.0}, {BACKUP: shock.toward}, value) == {}
    assert rs.moves(season_out, HURT, {LEAD: 1 - rs.MIN_MISSED / 2}, {BACKUP: 300.0}, {BACKUP: 30.0}, value) == {}
    out_for_season = rs.moves(season_out, HURT, {LEAD: 0.0}, {BACKUP: 300.0}, {BACKUP: 30.0}, value)[BACKUP][0]
    half = rs.moves(season_out, HURT, {LEAD: 0.5}, {BACKUP: 300.0}, {BACKUP: 30.0}, value)[BACKUP][0]
    assert out_for_season > half > 0                                                    # "out for the season" counts fully


def test_the_stand_in_loses_value_when_the_starter_comes_back():
    data = _lead_out(fakes.fake_season(), range(4, 7))                                   # back in week 7
    ranks = dict(data.expert_ranks)
    ranks.update({(BACKUP, w): 12 for w in range(4, 7)})                                 # the market bought the stand-in
    data = replace(data, expert_ranks=MappingProxyType(ranks))

    shock, = [s for s in _mine(data, 7) if s.kind == "starter_back"]
    loss = rs.moves(data, 7, {}, {BACKUP: 500.0}, {BACKUP: 12.0}, lambda position, r: 1000.0 / r)[BACKUP][0]

    assert (shock.other, shock.up, shock.anchor) == (LEAD, False, 12.0)
    assert shock.toward == rs.market_ranks(data.as_of(4), 4)[BACKUP]                     # where he stood before
    assert loss < 0


def test_an_untagged_absence_is_not_a_starter_coming_back():
    """No injury tag while he sat (a scratch, a trade, a release): his return is not news for the stand-in."""
    data = fakes.patched(fakes.fake_season(), projected={(LEAD, w): None for w in range(4, 7)})
    ranks = {**data.expert_ranks, **{(BACKUP, w): 12 for w in range(4, 7)}}
    data = replace(data, expert_ranks=MappingProxyType(ranks))

    assert not [s for s in rs.events(data, 7) if s.kind == "starter_back" and s.other == LEAD]


def test_the_lifted_back_is_starred_with_a_public_reason(season_out):
    lifted, lead = _value(season_out, BACKUP, HURT), season_out.players[LEAD].name

    assert lifted.hype_reason.startswith(f"{lead} went out")
    assert replay_views.player_ref(lifted)["hype"] == {"reason": lifted.hype_reason}
    assert replay_views.player_ref(_value(season_out, THIRD, HURT))["hype"] is None          # not the one who gains
    assert replay_views.player_ref(_value(season_out, BACKUP, HURT - 1))["hype"] is None     # nothing yet


def test_hype_reasons_name_public_events_only_for_moves_up():
    shock = rs.Shock(1, "RB", "teammate_out", 5, 30.0, 5.0, True, 2, 5)

    assert rs.reason(shock, "Puka Nacua", 1.0) == "Puka Nacua went out for the season"
    assert rs.reason(shock, "Puka Nacua", 0.5) == "Puka Nacua went out"
    assert rs.reason(replace(shock, kind="teammate_left"), None, 1.0) == "a teammate left the team"
    assert rs.reason(replace(shock, kind="role_won", other=None), None, 1.0) == "he won the backfield"
    assert rs.reason(replace(shock, kind="role_won", position="WR"), None, 1.0) == "he won a bigger target share"
    assert rs.reason(replace(shock, kind="starter_back", up=False), "Puka Nacua", 1.0) is None


def test_a_starter_out_again_is_not_back():
    data = _lead_out(_lead_out(fakes.fake_season(), range(4, 7)), [9])                   # back in week 7, out in 9
    ranks = {**data.expert_ranks, **{(BACKUP, w): 12 for w in range(4, 7)}}               # the stand-in was bought
    data = replace(data, expert_ranks=MappingProxyType(ranks))

    assert [s.kind for s in _mine(data, 7) if s.other == LEAD] == ["starter_back"]
    assert not [s for s in rs.events(data, 9) if s.kind == "starter_back" and s.other == LEAD]


def test_unlisted_teammates_are_split_by_their_projections(season_out):
    ranks = {key: rank for key, rank in season_out.expert_ranks.items() if key[0] not in (BACKUP, THIRD)}
    hot = fakes.patched(replace(season_out, expert_ranks=MappingProxyType(ranks)),       # both unlisted: tied
                        projected={(THIRD, w): 30.0 for w in range(HURT - 3, HURT)})      # the third back plays more

    assert [s.player_id for s in rs.events(hot, HURT) if s.other == LEAD] == [THIRD]     # not the lower id


def test_a_backup_who_takes_the_job_in_week_1_wins_and_the_starter_loses(monkeypatch):
    usage = dict(fakes.fake_season().usage)
    usage[(LEAD, 1)] = Usage(30.0, 1, 0.03, 4)
    usage[(BACKUP, 1)] = Usage(80.0, 5, 0.12, 22)
    data = replace(fakes.fake_season(), usage=MappingProxyType(usage))
    monkeypatch.setitem(rs.GAMMA, ("role_lost", "RB"), 0.2)
    monkeypatch.setitem(rs.GAMMA, ("role_won", "RB"), 0.2)

    won, lost = _mine(data, 2), _mine(data, 2, LEAD)
    healthy, rank = {BACKUP: 200.0, LEAD: 900.0}, {BACKUP: 30.0, LEAD: 3.0}
    moved = rs.moves(data, 2, {}, healthy, rank, lambda position, r: 1000.0 / r)

    assert [s.kind for s in won] == ["role_won"] and [s.kind for s in lost] == ["role_lost"]
    assert won[0].toward < won[0].anchor and lost[0].toward > lost[0].anchor
    assert moved[BACKUP][0] > 0 > moved[LEAD][0]                                         # losers lose, symmetrically


def test_one_big_game_without_an_event_moves_nothing():
    data = fakes.fake_season()
    hot = fakes.patched(data, actual={(THIRD, 4): 45.0})                                 # points only: no role, no injury

    assert rs.events(hot, 5) == rs.events(data, 5)
    assert _value(hot, THIRD, 5).shock is None and _value(hot, THIRD, 5).shock_chart == 0.0


def test_cpus_price_deals_with_the_shocked_value(season_out, monkeypatch):
    shocked = _value(season_out, BACKUP, HURT)
    monkeypatch.setattr(rs, "GAMMA", dict.fromkeys(rs.GAMMA, 0.0))
    rv.clear_cache()
    plain = _value(season_out, BACKUP, HURT)

    assert shocked.chart > plain.chart and plain.shock is None      # the one value trades and CPUs read


@pytest.mark.parametrize("week", [2, 5, HURT, 10])
def test_shocks_never_read_later_weeks(season_out, week):
    other = fakes.future_changed(season_out, after_week=week)
    league = rv.league_settings(fakes.fake_settings())

    assert rs.events(season_out, week) == rs.events(other, week) == rs.events(season_out.as_of(week), week)
    mine = {v.player_id: (v.chart, v.shock, replay_views.player_ref(v)["hype"])
            for v in rv.knowledge(season_out, league, week, END).values}
    theirs = {v.player_id: (v.chart, v.shock, replay_views.player_ref(v)["hype"])
              for v in rv.knowledge(other, league, week, END).values}
    assert mine == theirs                                               # the hype star too


def test_a_tag_during_his_bye_week_is_news_at_once(season_out):
    assert fakes.fake_season().on_bye(LEAD, HURT)                                         # DAL's bye is week HURT
    assert rs.absence(season_out, LEAD, HURT) == HURT


def test_the_week_before_the_injury_shows_nothing(season_out):
    assert not _mine(season_out, HURT - 1) and _value(season_out, BACKUP, HURT - 1).shock is None
