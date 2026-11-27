"""replay_league_size: a league under 12 teams prices the players it leaves unrostered like a 12-team league's."""
import pytest

from services import replay_chart, replay_league_size as size, replay_state as rs, replay_values as rv
from tests import replay_fakes as fakes

SUPERFLEX = {**rs.DEFAULT_STARTERS, "SUPERFLEX": 1}


def league(teams: int, starters=SUPERFLEX):
    return rv.league_settings(fakes.fake_settings(teams=teams, starters=starters))


@pytest.mark.parametrize("teams", [12, 14, 16])
def test_twelve_teams_or_more_keep_the_chart_as_it_is(teams):
    data = fakes.fake_season()
    assert size.rank_map(league(teams)) == {} and size.sized(data, league(teams)) is data


@pytest.mark.parametrize("teams", [4, 8, 10])
@pytest.mark.parametrize("starters", [SUPERFLEX, dict(rs.DEFAULT_STARTERS)])
def test_starters_keep_their_rank_and_the_first_unrostered_player_takes_the_twelve_team_ones(teams, starters):
    here, base = size.depth(league(teams, starters)), size.depth(league(12, starters))
    for position, ranks in size.rank_map(league(teams, starters)).items():
        first_starters, rostered = here[position]
        assert ranks[:first_starters] == tuple(float(n) for n in range(1, first_starters + 1))
        assert ranks[rostered] == pytest.approx(base[position][1] + 1)               # the first unrostered player
        assert all(a < b for a, b in zip(ranks, ranks[1:]))                           # order kept


def test_superflex_in_eight_teams_uses_two_starting_qbs_a_team():
    starters, rostered = size.depth(league(8))["QB"]
    assert starters == 16 and rostered > starters
    assert size.depth(league(8, dict(rs.DEFAULT_STARTERS)))["QB"][0] == 8


def test_a_small_leagues_waiver_qb_is_worth_less_and_its_starters_are_untouched():
    data = fakes.fake_season()
    small, base = league(8), league(12)
    rv.clear_cache()
    eight = rv.knowledge(data, small, 4, 16)
    twelve = rv.knowledge(data, base, 4, 16)
    rv.clear_cache()
    qbs = sorted((v for v in twelve.values if v.position == "QB"), key=lambda v: -v.healthy_chart)
    starters, rostered = size.depth(small)["QB"]
    top, deep = qbs[0], [v for v in qbs[starters + 2:] if v.healthy_chart > 0]
    assert eight.by_id[top.player_id].healthy_chart == pytest.approx(twelve.by_id[top.player_id].healthy_chart, rel=0.02)
    assert deep and all(eight.by_id[v.player_id].healthy_chart < v.healthy_chart for v in deep)


def test_the_sized_view_moves_adp_only_and_reads_nothing_from_the_week_on():
    data = fakes.fake_season()
    view = size.sized(data, league(8))
    assert {pid: (p.position, p.name) for pid, p in view.players.items()} == \
        {pid: (p.position, p.name) for pid, p in data.players.items()}
    assert replay_chart.slot_curve(view)["K"] == replay_chart.slot_curve(data)["K"]   # K and DST untouched
    for week in (3, 8):
        rv.clear_cache()
        now = rv.knowledge(data, league(8), week, 16)
        rv.clear_cache()
        changed = rv.knowledge(fakes.future_changed(data, week), league(8), week, 16)
        assert [(v.player_id, v.chart) for v in now.values] == [(v.player_id, v.chart) for v in changed.values]
    rv.clear_cache()
