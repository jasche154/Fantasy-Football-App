"""sim_injuries: sampling from the stored fit (no database)."""
import numpy as np
import pytest

from services import sim_injuries as si
from services.replay_pool import PRACTICE, TAGS, Injury, hard_tag_returns
from services.sim_injuries_fit import body_part, kaplan_meier

SKILL = ("QB", "RB", "WR", "TE", "K")


def dice(seed: int = 7) -> np.random.Generator:
    return np.random.default_rng(seed)


def _valid(injury: Injury) -> None:
    assert injury.tag in TAGS
    assert injury.practice is None or injury.practice in PRACTICE
    assert injury.injury and len(injury.injury) <= si.MAX_BODY


def test_same_dice_same_draws():
    for position in SKILL:
        assert si.draw(dice(3), position) == si.draw(dice(3), position)
        assert si.phantom(dice(4), position) == si.phantom(dice(4), position)
        assert si.preseason(dice(5), position) == si.preseason(dice(5), position)
        assert si.open_spell(dice(6), position, "IR", 3) == si.open_spell(dice(6), position, "IR", 3)


def test_hazard_ordering_and_defense():
    assert si.hazard("RB") > si.hazard("QB") > si.hazard("K") > 0
    assert si.hazard("DST") == 0.0
    assert si.phantom(dice(), "DST") is None and si.preseason(dice(), "DST") is None
    with pytest.raises(ValueError):
        si.hazard("LB")


def test_spells_and_tags_have_real_shapes():
    rolls = dice(11)
    for position in SKILL:
        for _ in range(300):
            spell = si.draw(rolls, position)
            assert spell.games_out >= 0 and 0 < spell.exit_share <= 1
            for missed in range(spell.games_out):
                injury = si.tag(spell, missed)
                _valid(injury)
                assert injury.hard and injury.tag in ("O", "IR")
                assert injury.practice is None if injury.tag == "IR" else injury.practice in PRACTICE
            back = si.return_tag(spell)
            assert (back is not None) == spell.questionable_back
            if back:
                _valid(back)
                assert back.tag == "Q" and back.practice in PRACTICE


def test_ir_only_for_long_spells_and_held_to_the_return():
    ir = si.stored()["ir"]
    short = si.Spell(ir["min_games"] - 1, 1.0, "Ankle", False)
    assert all(si.tag(short, m).tag == "O" for m in range(short.games_out))
    long = si.Spell(12, 0.4, "Knee", False)
    tags = [si.tag(long, m).tag for m in range(long.games_out)]
    first = tags.index("IR")
    assert set(tags[first:]) == {"IR"} and long.games_out - first >= ir["min_stint"]
    with pytest.raises(ValueError):
        si.tag(long, -1)


def test_banged_up_players_miss_nothing_and_are_listed_back():
    rolls = dice(1)
    banged = [s for s in (si.draw(rolls, "WR") for _ in range(2000)) if s.games_out == 0]
    assert banged and all(s.questionable_back and s.exit_share < 1 for s in banged)


def test_phantoms_are_q_or_d_at_a_small_rate():
    rolls = dice(2)
    listed = [i for i in (si.phantom(rolls, "WR") for _ in range(4000)) if i is not None]
    assert 0 < len(listed) < 400
    for injury in listed:
        _valid(injury)
        assert injury.tag in ("Q", "D") and not injury.hard


def test_open_spell_never_negative_and_longer_on_ir():
    rolls = dice(9)
    for tag in ("O", "IR", "PUP"):
        for weeks in (0, 1, 2, 4, 6, 9, 15):
            assert all(si.open_spell(rolls, "RB", tag, weeks) >= 0 for _ in range(50))
    on_o = np.mean([si.open_spell(rolls, "WR", "O", 1) for _ in range(2000)])
    on_ir = np.mean([si.open_spell(rolls, "WR", "IR", 1) for _ in range(2000)])
    assert on_ir > on_o
    assert si.open_spell(rolls, "DST", "O", 2) == 0
    with pytest.raises(ValueError):
        si.open_spell(rolls, "RB", "Q", 1)


def test_preseason_spells_miss_at_least_one_game():
    rolls = dice(4)
    spells = [s for s in (si.preseason(rolls, "RB") for _ in range(3000)) if s is not None]
    assert 0 < len(spells) < 300
    assert all(s.games_out >= 1 and s.exit_share == 1.0 for s in spells)


def test_round_trip_through_hard_tag_returns():
    """A plan built from the sim's tags reads back the right return weeks, a bye inside the absence included."""
    pid, last_week, bye = 1, 18, 7
    spell = si.Spell(games_out=5, exit_share=0.5, body="Knee", questionable_back=True)
    hurt_week = 4
    team_weeks = [w for w in range(1, last_week + 1) if w != bye]
    missed_weeks = team_weeks[team_weeks.index(hurt_week) + 1:][:spell.games_out]
    back_week = team_weeks[team_weeks.index(missed_weeks[-1]) + 1]
    injuries = {(pid, w): si.tag(spell, i) for i, w in enumerate(missed_weeks)}
    injuries[(pid, bye)] = si.tag(spell, missed_weeks.index(bye + 1))       # carried over the bye
    injuries[(pid, back_week)] = si.return_tag(spell)
    actual = {(pid, w): 10.0 for w in team_weeks if w not in missed_weeks}
    returns = hard_tag_returns(injuries, actual, last_week)
    assert returns == {(pid, missed_weeks[0]): back_week}


def test_kaplan_meier_keeps_censored_mass_on_the_longest():
    pmf = dict((v, p) for v, p in kaplan_meier([(1, False), (1, False), (3, False), (5, True)]))
    assert pmf[1] == pytest.approx(0.5) and pmf[3] == pytest.approx(0.25) and pmf[5] == pytest.approx(0.25)


def test_body_part_cleanup():
    assert body_part("right Shoulder") == "Shoulder"
    assert body_part("Not injury related - personal matter") is None
    assert body_part("Illness") is None
    assert body_part("knee, ankle") == "Knee"


def _season(played: tuple[int, ...], tagged: dict[int, str]):
    """A 6-week season of one WR (id 1, team AAA): his played weeks and his tags by week."""
    from types import MappingProxyType

    from services.replay_pool import PoolPlayer, SeasonData
    from services.schedule_strength import Game, Schedule

    games = tuple(Game(w, "AAA", "BBB", None, None, None, None) for w in range(1, 7))
    opponents = MappingProxyType({**{("AAA", w): "BBB" for w in range(1, 7)}, **{("BBB", w): "AAA" for w in range(1, 7)}})
    return SeasonData(2024, "half", MappingProxyType({1: PoolPlayer(1, "W", "WR", 10.0, 1.0, "AAA")}),
                      Schedule(games, opponents), MappingProxyType({(1, w): 8.0 for w in played}),
                      MappingProxyType({}), MappingProxyType({1: tuple((w, "AAA") for w in played)}), 6,
                      injuries=MappingProxyType({(1, w): Injury(t, "DNP", "Knee") for w, t in tagged.items()}))


def test_an_absence_that_ends_unseen_is_censored_and_only_a_season_end_carries_over():
    """Hurt after week 1, tagged O in weeks 2-3, then an untagged miss in week 4: he was out at least 2 games
    (right-censored), and the absence did not run out the season, so it is not carried into the next."""
    from services.sim_injuries_fit import Tally, walk

    tally = Tally()
    walk(_season((1, 5, 6), {2: "O", 3: "O"}), 1, "WR", tally, False)
    (spell,) = tally.spells
    assert (spell.length, spell.censored, spell.season_end) == (2, True, False)
    tally = Tally()
    walk(_season((1,), {w: "IR" for w in range(2, 7)}), 1, "WR", tally, False)
    (spell,) = tally.spells
    assert (spell.length, spell.censored, spell.season_end) == (5, True, True)
    tally = Tally()
    walk(_season((1, 4, 5, 6), {2: "O", 3: "O"}), 1, "WR", tally, False)
    assert [(s.length, s.censored, s.season_end) for s in tally.spells] == [(2, False, False)]
