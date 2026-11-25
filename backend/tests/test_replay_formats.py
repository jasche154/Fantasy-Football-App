"""Replay League formats: superflex, double flex and custom slot counts (services/replay_slots.py and every
place that reads a lineup: legality, the optimizer, the CPU draft, values and a whole simulated season)."""
from collections import Counter

import pytest

from services import (
    replay_adp_format as af, replay_chart, replay_draft as rd, replay_season, replay_slots, replay_state as rs,
    replay_superflex,
)
from services import replay_values as rv
from tests import replay_fakes as fakes
from tests.test_replay_draft import play_out
from tests.test_replay_game import play

DATA = fakes.fake_season()
SUPERFLEX = fakes.fake_settings(starters=replay_slots.PRESETS["superflex"])
DOUBLE_FLEX = fakes.fake_settings(starters=replay_slots.PRESETS["double_flex"])
STANDARD = fakes.fake_settings()
QB = [fakes.player_id("QB", rank) for rank in range(1, 4)]
RB = [fakes.player_id("RB", rank) for rank in range(1, 5)]
WR = [fakes.player_id("WR", rank) for rank in range(5, 9)]
TE = [fakes.player_id("TE", 1)]
KDST = [fakes.player_id("K", 1), fakes.player_id("DST", 1)]


def test_superflex_takes_any_skill_position_and_flex_no_quarterback():
    assert replay_slots.eligible("SUPERFLEX") == ("QB", "RB", "WR", "TE")
    assert replay_slots.eligible("FLEX") == ("RB", "WR", "TE")
    assert replay_slots.eligible("QB") == ("QB",)
    assert rs.SLOTS == ("QB", "RB", "WR", "TE", "FLEX", "SUPERFLEX", "K", "DST")


def test_presets_differ_from_standard_only_where_their_names_say():
    standard = dict(replay_slots.PRESETS["standard"])

    assert standard == dict(rs.DEFAULT_STARTERS)
    assert dict(replay_slots.PRESETS["superflex"]) == {**standard, "SUPERFLEX": 1}
    assert dict(replay_slots.PRESETS["double_flex"]) == {**standard, "FLEX": 2}
    assert all(replay_slots.problem(preset) is None for preset in replay_slots.PRESETS.values())


@pytest.mark.parametrize("starters, message", [
    ({"QB": 0, "RB": 2}, "QB or SUPERFLEX"),
    ({"QB": 1, "K": 1, "DST": 1}, "RB, WR, TE or FLEX"),
    ({"QB": 4, "RB": 2}, "0 to 3"),
    ({"QB": 1, "RB": -1, "WR": 2}, "0 to 3"),
    ({"QB": 3, "RB": 3, "WR": 3, "TE": 3, "FLEX": 1}, "at most 12"),
    ({"QB": 1, "IDP": 1}, "Unknown lineup slots"),
])
def test_a_lineup_outside_the_bounds_is_refused(starters, message):
    assert message in replay_slots.problem(starters)


def test_a_superflex_only_lineup_is_allowed():
    assert replay_slots.problem({"SUPERFLEX": 2, "RB": 2, "WR": 3}) is None


@pytest.mark.parametrize("positions, missing", [
    (["QB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], ["FLEX", "SUPERFLEX"]),
    (["QB", "RB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], ["SUPERFLEX"]),
    (["QB", "QB", "RB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], []),
    (["QB", "RB", "RB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], []),          # a back plays superflex
    (["QB", "QB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], ["FLEX"]),          # a QB can't play FLEX
    (["RB", "RB", "RB", "WR", "WR", "TE", "K", "DST"], ["QB", "SUPERFLEX"]),     # the QB slot needs a QB
])
def test_missing_slots_fills_flex_first_then_superflex(positions, missing):
    assert rv.missing_slots(positions, SUPERFLEX) == missing
    assert rv.lineup_holes(positions, SUPERFLEX) == len(missing)
    assert rv.can_field_lineup(positions, SUPERFLEX) is (not missing)


def test_double_flex_needs_two_spare_backs_receivers_or_tight_ends():
    base = ["QB", "RB", "RB", "WR", "WR", "TE", "K", "DST"]

    assert rv.missing_slots(base, DOUBLE_FLEX) == ["FLEX", "FLEX"]
    assert rv.missing_slots([*base, "TE"], DOUBLE_FLEX) == ["FLEX"]
    assert rv.missing_slots([*base, "TE", "WR", "QB"], DOUBLE_FLEX) == []
    assert rv.position_cap("RB", DOUBLE_FLEX) == rv.position_cap("RB", STANDARD) + 1


def test_superflex_raises_the_quarterback_cap_only():
    assert rv.position_cap("QB", SUPERFLEX) == rv.position_cap("QB", STANDARD) + 1 == 3
    assert all(rv.position_cap(p, SUPERFLEX) == rv.position_cap(p, STANDARD) for p in ("RB", "WR", "TE", "K"))


def test_the_optimizer_starts_the_second_quarterback_in_superflex():
    know = fakes.fake_knowledge(1, SUPERFLEX)
    roster = [*QB[:2], *RB, *WR, *TE, *KDST]

    lineup = rv.best_lineup(know, roster, SUPERFLEX)
    by_projection = sorted(QB[:2], key=lambda pid: -know.by_id[pid].projection)

    assert lineup["QB"] == by_projection[:1] and lineup["SUPERFLEX"] == by_projection[1:]
    assert len(lineup["FLEX"]) == 1 and know.by_id[lineup["FLEX"][0]].position in ("RB", "WR", "TE")
    assert "SUPERFLEX" not in rv.best_lineup(know, roster, STANDARD)


def test_double_flex_starts_two_flex_players():
    know = fakes.fake_knowledge(1, DOUBLE_FLEX)
    state = fakes.drafted_state(DOUBLE_FLEX)
    lineup = replay_season.starters_for(state, DOUBLE_FLEX, know, rs.user_team(state).id)

    assert len(lineup["FLEX"]) == 2 and "SUPERFLEX" not in lineup
    assert all(know.by_id[pid].position in rv.FLEX_POSITIONS for pid in lineup["FLEX"])


def test_a_user_may_set_a_quarterback_or_a_receiver_in_superflex_but_not_a_kicker():
    state = fakes.drafted_state(SUPERFLEX)
    know = fakes.fake_knowledge(1, SUPERFLEX)
    roster = [know.by_id[pid] for pid in rs.user_team(state).roster]
    qbs = [v.player_id for v in roster if v.position == "QB"]
    receiver = next(v.player_id for v in roster if v.position == "WR")
    kicker = next(v.player_id for v in roster if v.position == "K")

    assert len(qbs) >= 2                                          # the straight-ADP draft keeps lineups legal
    set_qb = replay_season.set_lineup(state, SUPERFLEX, know, {"QB": qbs[:1], "SUPERFLEX": qbs[1:2]})
    set_wr = replay_season.set_lineup(state, SUPERFLEX, know, {"SUPERFLEX": [receiver]})
    assert set_qb.lineup["SUPERFLEX"] == (qbs[1],) and set_wr.lineup["SUPERFLEX"] == (receiver,)
    with pytest.raises(ValueError, match="can't play in the SUPERFLEX slot"):
        replay_season.set_lineup(state, SUPERFLEX, know, {"SUPERFLEX": [kicker]})
    with pytest.raises(ValueError, match="takes 1"):
        replay_season.set_lineup(state, SUPERFLEX, know, {"SUPERFLEX": qbs[:2]})


def _draft(settings: rs.Settings, seed: int) -> rs.State:
    return play_out(rd.new_game(settings, seed), settings, DATA, seed)


def _first_qb_picks(state: rs.State) -> list[int]:
    first: dict[int, int] = {}
    for pick in state.picks:
        if DATA.players[pick.player_id].position == "QB":
            first.setdefault(pick.team_id, pick.overall)
    return sorted(first.values())


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_cpus_draft_two_quarterbacks_early_in_superflex(seed):
    superflex, standard = _draft(SUPERFLEX, seed), _draft(STANDARD, seed)

    for team in superflex.teams:
        positions = Counter(DATA.players[pid].position for pid in team.roster)
        assert 2 <= positions["QB"] <= rv.position_cap("QB", SUPERFLEX), (team.name, positions)
        assert rv.can_field_lineup(list(positions.elements()), SUPERFLEX)
    # half the QB starters (one per team) are gone far sooner than in a one-QB league
    assert _first_qb_picks(superflex)[5] < _first_qb_picks(standard)[5] * 0.7
    qbs_by_round_6 = sum(DATA.players[p.player_id].position == "QB" for p in superflex.picks if p.round <= 6)
    assert qbs_by_round_6 >= SUPERFLEX.teams


def test_without_a_2qb_list_quarterbacks_go_at_the_fitted_non_qb_slots():
    # the old stopgap (every QB's ADP x 0.4) is gone: QB n takes the pick of the average 2-QB list's slot n
    view = replay_chart.superflex_view(DATA)
    others = sorted(p.adp for p in DATA.players.values() if p.position != "QB" and p.adp is not None)
    qbs = sorted((p.adp, pid) for pid, p in DATA.players.items() if p.position == "QB" and p.adp is not None)
    slots = replay_superflex.fitted()["2qb"]

    for (_, qb), slot in zip(qbs, slots):
        assert view.players[qb].adp == pytest.approx(replay_superflex.pick_of_slot(others, slot))
    assert all(view.players[pid] == p for pid, p in DATA.players.items() if p.position != "QB")
    assert view.players[qbs[0][1]].adp < 5                                  # the top QB goes in the first round
    assert replay_chart.superflex_view(DATA) is view                        # cached: curves on it are too


def test_superflex_values_quarterbacks_higher_on_the_chart_and_over_replacement():
    standard, superflex = fakes.fake_knowledge(1, STANDARD), fakes.fake_knowledge(1, SUPERFLEX)
    qb, wr = fakes.player_id("QB", 6), fakes.player_id("WR", 1)

    assert rv.league_settings(SUPERFLEX).superflex and not rv.league_settings(STANDARD).superflex
    assert superflex.by_id[qb].chart > standard.by_id[qb].chart * 1.5
    assert superflex.replacement["QB"] < standard.replacement["QB"]
    assert superflex.by_id[qb].trade_value > standard.by_id[qb].trade_value
    assert superflex.by_id[wr].chart == standard.by_id[wr].chart          # non-QBs: exactly the regular league's


def test_superflex_values_see_no_hindsight():
    week = 6
    known = fakes.fake_knowledge(week, SUPERFLEX)
    changed = fakes.fake_knowledge(week, SUPERFLEX, fakes.future_changed(DATA, week))

    assert known.values == changed.values


@pytest.mark.parametrize("settings", [SUPERFLEX, DOUBLE_FLEX], ids=["superflex", "double_flex"])
def test_a_whole_season_plays_cleanly_in_other_formats(settings):
    run = play(settings, seed=5, active=True)

    assert run.faults == () and run.final.phase == "complete"
    lineups = [result.starters[team.id] for result in run.final.results for team in run.final.teams]
    flex = settings.starters.get("FLEX", 0)
    assert all(len(lineup.get("FLEX", ())) <= flex for lineup in lineups)
    if settings is SUPERFLEX:
        assert sum(DATA.players[pid].position == "QB" for lineup in lineups for pid in lineup["SUPERFLEX"]) \
            > len(lineups) * 0.7                                   # most teams start a QB there


TWO_QB = fakes.fake_settings(starters={**replay_slots.STANDARD, "QB": 2})


def test_two_quarterback_slots_count_as_superflex_everywhere():
    assert [replay_slots.two_qb(s.starters) for s in (SUPERFLEX, TWO_QB, STANDARD, DOUBLE_FLEX)] == [
        True, True, False, False]
    assert af.wanted("half", None, TWO_QB.starters) == "2qb"


@pytest.mark.parametrize("seed", [0, 1])
def test_a_two_qb_league_without_a_2qb_list_drafts_quarterbacks_from_the_boosted_view(seed, monkeypatch):
    assert DATA.adp_format != "2qb"
    seen: list = []
    view = replay_chart.superflex_view

    def spy(data):
        seen.append(data)
        return view(data)
    spy.cache_clear = view.cache_clear                       # the cache teardown still clears the real one
    monkeypatch.setattr(replay_chart, "superflex_view", spy)

    rd.cpu_pick(rd.new_game(TWO_QB, seed), TWO_QB, DATA, seed)

    assert seen, "a 2-QB league must draft through superflex_view when the season has no 2-QB list"


def test_a_two_qb_league_ranks_quarterbacks_by_the_superflex_view():
    standard, two_qb = fakes.fake_knowledge(1, STANDARD), fakes.fake_knowledge(1, TWO_QB)
    qb = fakes.player_id("QB", 6)

    assert two_qb.by_id[qb].pre_rank < standard.by_id[qb].pre_rank
    assert not rv.league_settings(TWO_QB).superflex      # its 2 QB slots set replacement levels, no extra slot


@pytest.mark.parametrize("settings", [SUPERFLEX, TWO_QB])
def test_the_draft_board_ranks_quarterbacks_where_cpus_draft_them(settings):
    from services.replay_views import sorted_board
    know = fakes.fake_knowledge(0, settings)
    board = sorted_board([know.by_id[p.player_id] for p in rd.available(rd.new_game(settings, 0), DATA)], "rank", None)
    cpu_view = rd._board(replay_chart.superflex_view(DATA.as_of(0)))

    def quarterbacks(order) -> list[int]:
        return [i for i, player in enumerate(order) if player.position == "QB"][:8]
    assert quarterbacks(board) == quarterbacks(cpu_view)
