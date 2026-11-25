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
