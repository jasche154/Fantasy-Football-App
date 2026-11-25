"""Superflex chart values (services/replay_superflex.py): RBs, WRs and TEs exactly as in a regular league of the
same scoring, each QB at the pick of the non-QB slot the 2-QB list puts him in."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart, replay_slots, replay_superflex as sf, replay_values as rv
from services.replay_pool import SeasonData
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
SUPERFLEX = fakes.fake_settings(starters=replay_slots.PRESETS["superflex"])
STANDARD = fakes.fake_settings()


def two_qb_season(data: SeasonData, keep_scoring: bool = True) -> SeasonData:
    """`data` as a superflex league loads it: players on a 2-QB list (QBs at 0.3 x their ADP, tight ends pushed
    back as a standard-scoring list does) and, unless an old save, the league's own list in scoring_adp."""
    factor = {"QB": 0.3, "TE": 1.3}
    players = {pid: p if p.adp is None else replace(p, adp=p.adp * factor.get(p.position, 1.0))
               for pid, p in data.players.items()}
    scoring = {pid: p.adp for pid, p in data.players.items() if p.adp is not None} if keep_scoring else {}
    return replace(data, players=MappingProxyType(players), adp_format="2qb", scoring_adp=MappingProxyType(scoring))


TWO_QB = two_qb_season(DATA)
OLD_SAVE = two_qb_season(DATA, keep_scoring=False)


def test_qb_slots_count_the_non_qbs_ahead_and_how_far_he_went_toward_the_next():
    board = [("RB", 1.0), ("QB", 2.0), ("WR", 3.0), ("QB", 4.5), ("WR", 5.0), ("QB", 9.0)]

    assert sf.qb_slots(board) == pytest.approx((1.5, 2.75, 7.0))
    assert sf.qb_slots([("QB", 1.0), ("RB", 2.0)]) == (0.5,)                 # before every non-QB


def test_a_slot_is_priced_between_its_neighbours_picks():
    picks = [3.0, 5.0, 9.0]

    assert [sf.pick_of_slot(picks, slot) for slot in (0.5, 1.0, 1.5, 2.25, 4.0)] == [2.0, 3.0, 4.0, 6.0, 10.0]


@pytest.mark.parametrize("week", [0, 1, 6])
def test_a_non_qbs_chart_is_identical_with_and_without_a_superflex_slot(week):
    superflex = fakes.fake_knowledge(week, SUPERFLEX, TWO_QB)
    regular = fakes.fake_knowledge(week, STANDARD, DATA)
    others = [v for v in superflex.values if v.position not in ("QB", "K", "DST")]

    assert others and all((v.chart, v.healthy_chart, v.star, v.form_chart) == (
        regular.by_id[v.player_id].chart, regular.by_id[v.player_id].healthy_chart,
        regular.by_id[v.player_id].star, regular.by_id[v.player_id].form_chart) for v in others)
    qb = fakes.player_id("QB", 6)
    assert superflex.by_id[qb].chart > regular.by_id[qb].chart * 1.5
    assert superflex.by_id[qb].pre_rank < regular.by_id[qb].pre_rank      # drafted among the non-QBs


def test_each_qb_takes_the_pick_of_the_non_qb_slot_the_2qb_list_puts_him_in():
    view = sf.view(TWO_QB)
    others = sorted(adp for pid, adp in TWO_QB.scoring_adp.items() if DATA.players[pid].position != "QB")
    qbs = sorted((p.adp, pid) for pid, p in TWO_QB.players.items() if p.position == "QB" and p.adp is not None)
    slots = sf.qb_slots((p.position, p.adp) for p in TWO_QB.players.values() if p.adp is not None)

    assert len(qbs) == len(slots)
    for (_, qb), slot in zip(qbs, slots):
        assert view.players[qb].adp == pytest.approx(sf.pick_of_slot(others, slot))
    tight_end = fakes.player_id("TE", 1)
    assert view.players[tight_end].adp == DATA.players[tight_end].adp         # the league's list, not the 2-QB one
    assert replay_chart.superflex_view(TWO_QB) is view


def test_a_save_without_the_scoring_list_converts_its_2qb_list():
    one = sf.one_qb(OLD_SAVE)
    others = sorted((p.adp, pid) for pid, p in OLD_SAVE.players.items() if p.position != "QB" and p.adp is not None)
    fitted = sf.fitted()[DATA.scoring]

    for k, (_, pid) in enumerate(others[:40], start=1):     # the k-th non-QB, behind an average 1-QB list's QBs
        assert one.players[pid].adp == k + sum(slot < k for slot in fitted)
    order = [pid for _, pid in others]
    assert sorted(order, key=lambda pid: one.players[pid].adp) == order       # the 2-QB list's order kept
    know, regular = fakes.fake_knowledge(1, SUPERFLEX, OLD_SAVE), fakes.fake_knowledge(1, STANDARD, DATA)

    def best_qb(k) -> float:
        return max(v.chart for v in k.values if v.position == "QB")
    assert know.values[0].chart > 900 and best_qb(know) > best_qb(regular) * 1.5


def test_one_qb_and_view_leave_a_regular_season_alone_and_cache():
    assert sf.one_qb(DATA) is DATA
    assert sf.one_qb(TWO_QB) is sf.one_qb(TWO_QB) and sf.view(TWO_QB) is sf.view(TWO_QB)


def test_superflex_values_from_a_2qb_list_see_no_hindsight():
    week = 6
    known = fakes.fake_knowledge(week, SUPERFLEX, TWO_QB)
    changed = fakes.fake_knowledge(week, SUPERFLEX, fakes.future_changed(TWO_QB, week))

    assert known.values == changed.values


def test_the_fitted_slots_put_qbs_far_earlier_in_2qb_drafts():
    fitted = sf.fitted()

    for name in ("2qb", "ppr", "half", "std"):
        assert len(fitted[name]) >= 24 and list(fitted[name]) == sorted(fitted[name])
    assert all(two < one for two, one in zip(fitted["2qb"], fitted["ppr"]))
    assert fitted["2qb"][9] < fitted["ppr"][0]                               # QB10 of 2-QB lists before QB1 of PPR


def test_a_borrowed_qb_moves_up_monotonically():
    moved = [sf.borrowed_qb_adp(adp) for adp in (5.0, 25.0, 60.0, 120.0, 250.0, 300.0)]

    assert moved == sorted(moved) and all(m < adp for m, adp in zip(moved, (5.0, 25.0, 60.0, 120.0, 250.0)))


def test_a_cpu_drafts_quarterbacks_where_the_2qb_list_takes_them():
    from services import replay_draft as rd

    state = rd.new_game(SUPERFLEX, 3)
    board = rd._board(replay_chart.superflex_view(TWO_QB.as_of(0)))
    first_qb = next(i for i, p in enumerate(board) if p.position == "QB")
    slots = sf.qb_slots((p.position, p.adp) for p in TWO_QB.players.values() if p.adp is not None)

    assert first_qb == int(slots[0])                    # as many non-QBs ahead of QB1 as in the 2-QB list
    assert rd.cpu_pick(state, SUPERFLEX, TWO_QB, 3) in TWO_QB.players


def test_replacement_and_values_over_it_read_the_league_scoring_priors():
    know = fakes.fake_knowledge(1, SUPERFLEX, TWO_QB)
    regular = fakes.fake_knowledge(1, STANDARD, DATA)
    back = fakes.player_id("RB", 1)

    assert know.by_id[back].ros_ppg == regular.by_id[back].ros_ppg          # priors from the league's list
    assert know.replacement["QB"] < regular.replacement["QB"] and rv.league_settings(SUPERFLEX).superflex
