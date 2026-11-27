"""An injured player's trade chart over his absence (replay_values._frozen + replay_chart_injury): the value he
had the week before falls once by the user's curve and climbs back to it the week he returns."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart_injury as injury, replay_news, replay_values as rv
from services.replay_pool import Injury, hard_tag_returns
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
SETTINGS = fakes.fake_settings()
END = SETTINGS.end_week
LEAGUE = rv.league_settings(SETTINGS)
PID = fakes.player_id("WR", 2)
GAMES = [w for w in range(1, END + 1) if DATA.schedule.opponent(DATA.team_at(PID, w), w) is not None]
START = next(w for w in GAMES if sum(g >= w for g in GAMES) == 13)        # 13 of his team's games left


def hurt(games: int):
    """DATA with PID out (O tag, no projection, no game) for his team's next `games` games from START, and a
    news draw whose report names his real return."""
    out = set(GAMES[GAMES.index(START):GAMES.index(START) + games])
    gone = {(PID, w) for w in out}
    projected = {k: v for k, v in DATA.projected.items() if k not in gone}
    actual = {k: v for k, v in DATA.actual.items() if k not in gone}
    injuries = {**DATA.injuries, **{key: Injury("O", "DNP", "Ankle") for key in gone}}
    data = replace(DATA, projected=MappingProxyType(projected), actual=MappingProxyType(actual),
                   injuries=MappingProxyType(injuries),
                   returns=MappingProxyType(hard_tag_returns(injuries, actual, DATA.last_week)))
    return data, 0, data.returns[(PID, START)]


@pytest.fixture(autouse=True)
def exact_news(monkeypatch):
    """This module prices the chart's discount curve on news that names the real week (the news noise is
    test_replay_news's)."""
    def exact(seed, pid, since, back, week, tag, last=None, first_tag=None):
        week_named = back if back and (last is None or back <= last) else None
        return replay_news.Report(since, week_named, "Out; expected back." if week_named else "Out for the season.")
    monkeypatch.setattr(replay_news, "report", exact)


def value(data, week: int, seed: int, pid: int = PID) -> rv.ReplayValue:
    return rv.knowledge(data, LEAGUE, week, END, seed).by_id[pid]


def loss(data, week: int, seed: int, before: float) -> float:
    return 1 - value(data, week, seed).chart / before


def test_the_player_is_a_healthy_starter_before():
    assert not any(tag.hard for (pid, _), tag in DATA.injuries.items() if pid == PID)
    assert value(DATA, START - 1, 0).healthy_chart > 300


def test_one_game_out_costs_five_to_eight_percent_and_he_is_back_at_his_value_the_next_week():
    data, seed, back = hurt(1)
    before = value(data, START - 1, seed).healthy_chart

    assert 0.05 <= loss(data, START, seed, before) <= 0.08
    assert value(data, back, seed).chart == pytest.approx(before)          # before his first game back


def test_three_of_thirteen_cost_five_to_twelve_percent():
    data, seed, _ = hurt(3)
    before = value(data, START - 1, seed).healthy_chart

    assert 0.05 <= loss(data, START, seed, before) <= 0.12


def test_eight_of_thirteen_cost_35_to_45_percent_then_less_every_week_and_comes_back_at_the_return_factor():
    data, seed, back = hurt(8)
    before = value(data, START - 1, seed).healthy_chart
    weeks = [w for w in range(START, back) if w in GAMES]
    losses = [loss(data, w, seed, before) for w in weeks]
    factor = injury.return_factor(8)

    assert factor == injury.RETURN_LONG < 1
    assert 0.35 <= 1 - (1 - losses[0]) / factor <= 0.45            # the user's discount alone, on frozen x factor
    assert all(later < earlier for earlier, later in zip(losses, losses[1:]))
    frozen = value(data, weeks[-1], seed).healthy_chart / before                  # frozen all along (x the factor),
    assert frozen / factor in (pytest.approx(1.0, abs=1e-3), pytest.approx(rv.replay_chart.BYE_BUMP, abs=1e-3))
    returned = value(data, back, seed)                                             # the week he returns:
    assert returned.chart / before in (pytest.approx(factor, abs=0.02), pytest.approx(factor * rv.replay_chart.BYE_BUMP, abs=0.02))
    assert returned.chart < before                                                 # below his pre-injury value


def test_a_short_absence_comes_back_at_full_value():
    assert [injury.return_factor(games) for games in (1, 2, 3, 4)] == [1.0] * 4
    assert injury.return_factor(5) == pytest.approx(0.9) and injury.return_factor(12) == injury.RETURN_LONG


def test_the_frozen_value_sees_no_hindsight():
    data, seed, back = hurt(8)
    week = START + 2

    assert value(data, week, seed) == value(fakes.future_changed(data, week), week, seed)


def spell(data, pid: int, tagged: range, gone: range, tag: str = "O"):
    """`data` with `pid` hard-tagged (and unprojected) in `tagged` and no stat line in `gone`."""
    drop = {(pid, w) for w in gone}
    actual = {k: v for k, v in data.actual.items() if k not in drop}
    projected = {k: v for k, v in data.projected.items() if k[0] != pid or k[1] not in tagged}
    injuries = {**data.injuries, **{(pid, w): Injury(tag, "DNP", "Knee") for w in tagged}}
    return replace(data, actual=MappingProxyType(actual), projected=MappingProxyType(projected),
                   injuries=MappingProxyType(injuries),
                   returns=MappingProxyType(hard_tag_returns(injuries, actual, data.last_week)))


@pytest.mark.parametrize("case", ["no stat line", "a backup with snaps", "released while out"])
def test_an_absence_ends_the_first_game_he_is_available_for_stat_line_or_not(case):
    pid = fakes.player_id("WR", 3)
    tagged = range(5, 6) if case != "released while out" else range(5, 8)        # his tags stop, he never plays
    data = spell(DATA, pid, tagged, range(5, END + 1))
    if case == "a backup with snaps":
        data = replace(data, usage=MappingProxyType({**data.usage, **{
            (pid, w): replace(next(iter(data.usage.values())), snap_pct=0.2) for w in range(6, END + 1)}}))
    games = [w for w in range(tagged.stop, END + 1) if data.schedule.opponent(data.team_at(pid, w), w)]
    back = games[0]                                                               # his first untagged team game

    assert pid in rv._absences(data.as_of(back), back)                           # that week: still his old value
    for week in (back + 1, back + 5):
        assert pid not in rv._absences(data.as_of(week), week)
        now = value(data, week, 0, pid)
        assert now.chart == now.healthy_chart                                     # live again, nothing to discount
        assert now.healthy_chart == rv._base(data.as_of(week), LEAGUE, week, END, 0).rows[pid]["healthy_chart"]


def test_a_second_absence_freezes_at_his_live_value_after_the_first_return():
    pid = fakes.player_id("WR", 3)
    data = spell(DATA, pid, tagged=range(5, 6), gone=range(5, 7))               # out 5, back 6 without a stat line
    data = spell(data, pid, tagged=range(7, 9), gone=range(7, 9))                 # out again 7-8
    base = rv._base(data.as_of(6), LEAGUE, 6, END, 0)

    assert rv._absences(data.as_of(7), 7)[pid] == 6 and pid in base.live         # frozen at week 6, the return week
    frozen = rv._frozen(data.as_of(7), LEAGUE, 7, END, 0)[pid]
    bump = rv.replay_chart.BYE_BUMP if rv.replay_chart._bye_done(data, base.rows[pid]["team"], 6) else 1.0
    assert frozen == pytest.approx(base.live[pid] / bump)
    assert base.live[pid] != base.rows[pid]["healthy_chart"]                      # not the first absence's value


def test_the_frozen_value_and_chart_ignore_everything_after_the_week():
    data, seed, back = hurt(8)
    week = START + 2
    later = spell(data, PID, tagged=range(back, back + 3), gone=range(back, back + 3))   # a later return
    now = value(data, week, seed)

    assert now == value(fakes.future_changed(data, week), week, seed)            # later games, tags, points
    assert now.healthy_chart == value(later, week, seed).healthy_chart           # his return week moved too
    live = rv._base(data.as_of(week), LEAGUE, week, END, 0).live[PID]
    assert now.healthy_chart != pytest.approx(live, abs=1.0)                      # the freeze is doing the work


def test_a_jump_in_league_freezes_a_player_hurt_before_its_draft():
    drafted, week = START + 3, START + 4
    data, seed, back = hurt(8)

    now = rv.knowledge(data, LEAGUE, week, END, seed, drafted=drafted).by_id[PID]
    before = rv._base(data.as_of(START - 1), LEAGUE, START - 1, END, START - 1).rows[PID]["healthy_chart"]

    assert now.healthy_chart in (pytest.approx(before, abs=0.1),
                                 pytest.approx(before * rv.replay_chart.BYE_BUMP, abs=0.1))


def test_a_superflex_league_freezes_an_injured_qb_at_his_superflex_value():
    sf = rv.league_settings(fakes.fake_settings(starters={**fakes.fake_settings().starters, "SUPERFLEX": 1}))
    qb = fakes.player_id("QB", 2)
    data = spell(DATA, qb, tagged=range(6, 9), gone=range(6, 9))
    regular = rv.knowledge(data, LEAGUE, 5, END, 0).by_id[qb].healthy_chart
    before = rv.knowledge(data, sf, 5, END, 0).by_id[qb].healthy_chart
    now = rv.knowledge(data, sf, 7, END, 0).by_id[qb]

    assert before > regular * 1.5
    assert now.healthy_chart in (pytest.approx(before, abs=0.1),
                                 pytest.approx(before * rv.replay_chart.BYE_BUMP, abs=0.1))


def test_as_of_of_an_as_of_copy_is_the_seasons_own_copy_and_never_regains_weeks():
    from services import replay_pool

    assert DATA.as_of(9).as_of(5) is DATA.as_of(5)
    early = DATA.as_of(5)
    assert early.as_of(9) is early and max(w for _, w in early.actual) < 5
    replay_pool.clear_cache()
    assert not replay_pool._SOURCE                                                # no stale season kept


def test_a_frozen_value_takes_the_bye_bump_once_and_never_tops_the_scale(monkeypatch):
    back = fakes.player_id("RB", 5)                                             # his bye is week 7
    for frozen, expected in ((500.0, 500.0 * rv.replay_chart.BYE_BUMP), (999.0, rv.replay_chart.CHART_MAX)):
        monkeypatch.setattr(rv, "_frozen", lambda *a, frozen=frozen, **k: {back: frozen})
        rv.clear_cache()
        assert rv.knowledge(DATA, LEAGUE, 9, END, 0).by_id[back].healthy_chart == pytest.approx(expected, abs=0.05)
    rv.clear_cache()


def test_a_player_frozen_before_his_bye_is_bumped_once_after_it():
    back = fakes.player_id("RB", 5)                                             # bye week 7, out weeks 5-9
    data = spell(DATA, back, tagged=range(5, 10), gone=range(5, 10))
    before = value(data, 4, 0, back).healthy_chart                              # no bye behind him yet

    for week in (6, 8, 9):
        bump = rv.replay_chart.BYE_BUMP if week > 7 else 1.0
        expected = min(rv.replay_chart.CHART_MAX, before * bump)
        assert value(data, week, 0, back).healthy_chart == pytest.approx(expected, abs=0.05)


def test_a_player_hurt_in_a_game_he_played_is_frozen_from_that_week():
    pid = fakes.player_id("WR", 3)
    data = spell(DATA, pid, tagged=range(5, 8), gone=range(6, 8))                # keeps his week-5 stat line

    assert rv._absences(data.as_of(7), 7)[pid] == 5                              # out from week 6, not week 5


def _full_week_frozen(data, league, week, end_week, drafted):
    """The freeze as a full `_base` of each absence's last week before it reads it (the slower reference)."""
    found = {}
    for pid, before in rv._absences(data, week).items():
        base = rv._base(data.as_of(before), league, before, end_week, min(drafted, before))
        if pid in base.rows:
            bumped = rv.replay_chart._bye_done(data, base.rows[pid]["team"], before)
            found[pid] = base.live.get(pid, base.rows[pid]["healthy_chart"]) / (rv.replay_chart.BYE_BUMP if bumped else 1.0)
    return found


def _deep_qb(data):
    """The 20th QB by ADP: past an 8-team league's starters, so the league-size taper moves his value."""
    return sorted((p.adp, pid) for pid, p in data.players.items() if p.position == "QB" and p.adp)[19][1]


def _freeze_season():
    data = spell(DATA, fakes.player_id("WR", 3), tagged=range(5, 6), gone=range(5, 7))
    data = spell(data, fakes.player_id("WR", 3), tagged=range(7, 9), gone=range(7, 9))
    data = spell(data, fakes.player_id("RB", 1), tagged=range(4, 9), gone=range(4, 9), tag="IR")
    data = spell(data, fakes.player_id("QB", 2), tagged=range(3, 7), gone=range(3, 7))
    return spell(data, _deep_qb(DATA), tagged=range(5, 9), gone=range(5, 9))


def _freeze_league(teams: int, superflex: bool):
    starters = {**SETTINGS.starters, "SUPERFLEX": 1} if superflex else SETTINGS.starters
    return rv.league_settings(replace(SETTINGS, teams=teams, starters=starters))


@pytest.mark.parametrize("drafted", [0, 3])
@pytest.mark.parametrize("superflex", [False, True])
@pytest.mark.parametrize("teams", [8, 12])
def test_the_freeze_reads_only_the_frozen_players_positions_and_gives_the_full_weeks_values(teams, superflex, drafted):
    """`_frozen` builds each frozen week's live charts for the frozen players' positions only (`_live`); the values,
    and so every chart of the week, are the ones a full week's build gives, in a tapered league (value curve and
    preseason board are then different seasons) and with a jump-in draft week. Nested absences included."""
    data, league = _freeze_season(), _freeze_league(teams, superflex)
    for week in (4, 6, 8, 10):
        rv.clear_cache()
        fast = rv._frozen(data.as_of(week), league, week, END, drafted)
        rv.clear_cache()
        assert fast and fast == _full_week_frozen(data.as_of(week), league, week, END, drafted)
    rv.clear_cache()


@pytest.mark.parametrize("superflex", [False, True])
def test_a_deep_qbs_frozen_value_carries_the_league_size_taper(superflex):
    """The freeze reads the league's own value curve: a QB past an 8-team league's starters is frozen lower than in
    a 12-team league."""
    data, deep, week = _freeze_season(), _deep_qb(DATA), 8
    rv.clear_cache()
    eight = rv._frozen(data.as_of(week), _freeze_league(8, superflex), week, END, 0)[deep]
    rv.clear_cache()
    twelve = rv._frozen(data.as_of(week), _freeze_league(12, superflex), week, END, 0)[deep]
    rv.clear_cache()
    assert 0 < eight < twelve


def test_a_dropped_season_frees_the_week_builds_and_the_freeze():
    """replay_pool.free_dependents (a season evicted) empties every cache that keys on a SeasonData or on a
    Knowledge built from one."""
    from services import replay_pool
    data = spell(DATA, fakes.player_id("WR", 3), tagged=range(5, 7), gone=range(5, 7))
    rv.clear_cache()
    rv.projector(rv.knowledge(data, LEAGUE, 6, END))
    caches = (rv._base, rv._live, rv._knowledge, rv._priors, rv.projector)
    assert all(cache.cache_info().currsize for cache in caches)
    replay_pool.free_dependents()
    assert not any(cache.cache_info().currsize for cache in caches)
