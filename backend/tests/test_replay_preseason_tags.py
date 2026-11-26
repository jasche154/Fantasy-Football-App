"""A week-1 IR, PUP or suspension is a preseason move (2021: Gus Edwards' ACL, J.K. Dobbins'): the draft board
shows it and CPUs wait on him. A week-1 O tag is game-week news, still unknown at the draft."""
from services import replay_draft as rd
from services.replay_pool import Injury
from tests import replay_fakes as fakes
from tests.test_replay_draft import play_out

HURT = fakes.player_id("RB", 18)         # ADP 52, like Gus Edwards (57)
GAME_WEEK = fakes.player_id("WR", 2)


def _season():
    return fakes.tagged(fakes.fake_season(), {(HURT, 1): Injury("IR", injury="Knee"), (GAME_WEEK, 1): Injury("O")})


def test_the_draft_knows_a_preseason_ir_tag_but_not_a_game_week_out():
    data = _season()
    known = data.as_of(0)
    assert known.injuries.get((HURT, 1)) == Injury("IR", injury="Knee") and (GAME_WEEK, 1) not in known.injuries

    know = fakes.fake_knowledge(0, data=data)
    hurt, fine = know.by_id[HURT], know.by_id[GAME_WEEK]
    assert hurt.injury.tag == "IR" and hurt.out and hurt.chart < hurt.healthy_chart
    assert fine.injury is None and not fine.out


def test_cpus_wait_on_a_player_a_preseason_tag_rules_out():
    settings = fakes.fake_settings(teams=12, draft_slot=12)
    taken = {}
    for data, label in ((fakes.fake_season(), "healthy"), (_season(), "hurt")):
        state = play_out(rd.new_game(settings, 3), settings, data, 3)
        taken[label] = next(n for n, pick in enumerate(state.picks, start=1) if pick.player_id == HURT)
    assert taken["hurt"] > taken["healthy"] + 24          # two rounds later at least


def _out_until(back: int, tag: str = "IR"):
    """The season with HURT out from a preseason `tag` (weeks 1 .. back - 1), really back in week `back`."""
    from dataclasses import replace
    from types import MappingProxyType
    data = fakes.tagged(fakes.fake_season(), {(HURT, w): Injury(tag, injury="Knee") for w in range(1, back)})
    return replace(data, returns=MappingProxyType({**data.returns, (HURT, 1): back}))


def test_the_draft_reports_a_preseason_ir_move_with_no_timetable_and_never_how_long():
    """Tester: Jayden Daniels sat on the board tagged IR with no news, then read "back week 6", then "out for the
    season". At the draft the news says he starts on IR; its guess comes from the public prior, not his return."""
    short, long = (fakes.fake_knowledge(0, data=_out_until(back)).by_id[HURT] for back in (5, 14))

    assert short.news is not None and short.news.vague and "no timetable" in short.news.text
    assert (short.news, short.chart) == (long.news, long.chart)              # the real return changes nothing
    assert short.chart < short.healthy_chart


def test_a_preseason_suspension_shows_its_length_at_the_draft():
    hurt = fakes.fake_knowledge(0, data=_out_until(5, "SUS")).by_id[HURT]

    assert hurt.news.back == 5 and hurt.news.text.startswith("Suspended")


def test_the_draft_and_week_one_tell_the_same_story():
    data = _out_until(14)
    draft, first = fakes.fake_knowledge(0, data=data).by_id[HURT], fakes.fake_knowledge(1, data=data).by_id[HURT]

    assert draft.news.vague and first.news.vague and first.news.back == draft.news.back
    assert abs(first.chart - draft.chart) < 0.25 * draft.chart
