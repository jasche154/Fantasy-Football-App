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


# (starters, rostered) league-wide on the shipped curves (replay_league_size.json), bench 6
DEPTH = {
    ("superflex", 8): {"QB": (16, 28), "RB": (17, 30), "WR": (23, 40), "TE": (8, 14)},
    ("superflex", 10): {"QB": (20, 35), "RB": (21, 37), "WR": (29, 51), "TE": (10, 18)},
    ("superflex", 12): {"QB": (24, 42), "RB": (25, 44), "WR": (35, 61), "TE": (12, 21)},
    ("regular", 8): {"QB": (8, 15), "RB": (17, 32), "WR": (23, 43), "TE": (8, 15)},
    ("regular", 10): {"QB": (10, 19), "RB": (21, 39), "WR": (29, 54), "TE": (10, 19)},
    ("regular", 12): {"QB": (12, 22), "RB": (25, 46), "WR": (35, 65), "TE": (12, 22)},
}


@pytest.mark.parametrize("lineup, teams", sorted(DEPTH))
def test_depth_counts_starters_and_rostered_players_on_the_shipped_curves(lineup, teams):
    starters = SUPERFLEX if lineup == "superflex" else dict(rs.DEFAULT_STARTERS)
    found = size.depth(league(teams, starters))
    assert {p: found[p] for p in DEPTH[(lineup, teams)]} == DEPTH[(lineup, teams)]


@pytest.mark.parametrize("starters", [SUPERFLEX, dict(rs.DEFAULT_STARTERS)])
def test_the_sized_view_keeps_the_starters_picks_and_only_pushes_the_rest_later(starters):
    """`sized` at 8 teams: each position's first `starters` players by ADP keep their pick, picks never go down in
    the original ADP order, nobody moves earlier than his own pick, the first player the league leaves unrostered
    is pushed strictly later (no taper at all would pass the rest), and a player without ADP stays without one."""
    data = fakes.fake_season()
    base = replay_chart.superflex_view(data) if "SUPERFLEX" in starters else data
    view, depth = size.sized(base, league(8, starters)), size.depth(league(8, starters))
    assert view is not base
    pushed = 0
    for position in ("QB", "RB", "WR", "TE"):
        drafted = sorted((p.adp, pid) for pid, p in base.players.items() if p.position == position and p.adp)
        picks = [view.players[pid].adp for _, pid in drafted]
        kept = depth[position][0]
        assert picks[:kept] == pytest.approx([adp for adp, _ in drafted[:kept]]), position
        assert all(a <= b for a, b in zip(picks, picks[1:])), position
        assert all(new >= old - 1e-9 for new, (old, _) in zip(picks, drafted)), position
        rostered = depth[position][1]
        if len(picks) > rostered:
            assert picks[rostered] > drafted[rostered][0], position
            pushed += 1
    assert pushed
    assert all(view.players[pid].adp is None for pid, p in base.players.items() if p.adp is None)


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


@pytest.mark.parametrize("starters", [SUPERFLEX, dict(rs.DEFAULT_STARTERS)])
@pytest.mark.parametrize("week", [0, 6])
def test_the_board_order_pre_rank_and_star_do_not_depend_on_league_size(starters, week):
    """The taper moves the value curve only: the board's rank order (`pre_rank`) and `star` (which feeds the
    form term) are the 12-team league's, as the CPU draft's raw ADP is. `pre_chart` is a value, so it is tapered
    like `chart`: the gap between the two (replay_personas, replay_mood) must not open from league size alone."""
    data = fakes.fake_season()
    rv.clear_cache()
    eight = rv.knowledge(data, league(8, starters), week, 16)
    twelve = rv.knowledge(data, league(12, starters), week, 16)
    rv.clear_cache()

    def board(know):
        return {v.player_id: (v.pre_rank, v.star) for v in know.values}
    assert board(eight) == board(twelve)
    _, curve, _ = rv.chart_view(data.as_of(week), league(8, starters))
    priced = replay_chart.pre_picks(curve)
    mine, theirs = ({v.player_id: v.pre_chart for v in know.values} for know in (eight, twelve))
    assert all(mine[pid] == round(replay_chart.pick_value(pick), 1) for pid, pick in priced.items() if pid in mine)
    assert mine != theirs                                   # the taper does reach the preseason value
