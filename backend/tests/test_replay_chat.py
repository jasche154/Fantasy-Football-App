"""Replay League: the league chat (services/replay_chat.py): who talks after a week, the caps, no repeats,
trade reactions, offer messages, old saves and the no-hindsight rule."""
from dataclasses import replace
from functools import lru_cache

import pytest

from services import replay_chat as chat, replay_personas, replay_season, replay_state as rs, replay_values as rv
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings(waiver_mode="none")
DATA = fakes.fake_season()


def knowledge(state: rs.State, data=DATA) -> rv.Knowledge:
    return rv.knowledge(data.as_of(state.week), rv.league_settings(SETTINGS), state.week, SETTINGS.end_week)


def start(seed: int = 0) -> rs.State:
    state = fakes.drafted_state(SETTINGS)
    personas = replay_personas.assign(seed, [t.id for t in state.teams if not t.is_user])
    return replace(state, teams=tuple(replace(t, persona=personas.get(t.id, "balanced"),
                                              hype=replay_personas.hype_for(personas.get(t.id, "balanced")))
                                      for t in state.teams))


@lru_cache(maxsize=8)
def season(seed: int = 0, weeks: int = SETTINGS.end_week) -> tuple[rs.State, ...]:
    """The state after each played week (index 0 = after week 1), with the chat posted each week."""
    state, played = start(seed), []
    for _ in range(weeks):
        state = replay_season.play_week(state, SETTINGS, DATA, knowledge(state), seed)
        state = chat.after_week(state, SETTINGS, knowledge(state), seed)
        played.append(state)
        if state.phase == "complete":
            break
    return tuple(played)


def lines_of(state: rs.State, week: int) -> list[rs.Chat]:
    return [c for c in state.chat if c.week == week]


def test_the_users_opponent_always_talks_to_him_and_a_week_is_capped():
    for seed in range(3):
        states = season(seed)
        for state in states[:SETTINGS.regular_season_weeks]:
            result = state.results[-1]
            user = rs.user_team(state).id
            opponent = next(b if a == user else a for a, b in result.matchups if user in (a, b))
            week = lines_of(state, result.week)
            assert 1 <= len(week) <= chat.MAX_WEEK_LINES
            assert [(c.team_id, c.to_team) for c in week if c.to_team is not None] == [(opponent, user)]
            assert all(not rs.team(state, c.team_id).is_user for c in week)


def test_lines_are_rendered_from_the_bank_in_the_managers_voice():
    final = season(0)[-1]
    assert len(final.chat) > SETTINGS.end_week
    for line in final.chat:
        template = chat.bank()[line.kind][rs.team(final, line.team_id).persona][line.line]
        assert "{" not in line.text and "}" not in line.text
        assert all(part in line.text for part in template.replace("}", "{").split("{")[::2] if part)


def test_the_final_crowns_the_champion_in_chat():
    final = season(0)[-1]
    assert final.phase == "complete"
    last = lines_of(final, final.week)
    kinds = {c.kind for c in last}
    assert kinds & {"champion", "runner_up"}
    if final.champion is not None and not rs.team(final, final.champion).is_user:
        assert any(c.kind == "champion" and c.team_id == final.champion for c in last)


def test_result_kinds_match_the_margin():
    for state in season(1):
        result = state.results[-1]
        for line in lines_of(state, result.week):
            if line.kind not in ("blowout_win", "close_win", "close_loss", "blowout_loss"):
                continue
            pair = next(p for p in result.matchups if line.team_id in p)
            mine = result.scores[line.team_id]
            theirs = result.scores[pair[0] if pair[1] == line.team_id else pair[1]]
            assert line.kind.endswith("win") == (mine > theirs)
            if line.to_team is None:
                margin = abs(mine - theirs)
                assert margin < chat.CLOSE_MARGIN if line.kind.startswith("close") else margin >= chat.BLOWOUT_MARGIN


def test_a_bucket_never_repeats_until_every_line_is_used():
    state = start()
    team = next(t for t in state.teams if t.persona == "panicky")
    values = dict.fromkeys(chat.PLACEHOLDERS["boom"], "X")
    size = len(chat.bank()["boom"]["panicky"])
    for _ in range(size + 3):
        state = chat.post(state, team, "boom", values, seed=7)
    used = [c.line for c in state.chat]

    assert sorted(used[:size]) == list(range(size))
    assert len(set(used[size:])) == 3


def test_a_shop_message_goes_to_the_user_and_names_the_player():
    state = start()
    cpu = next(t for t in state.teams if t.persona == "panicky")
    pid = cpu.roster[0]
    know = knowledge(state)
    after = chat.shop_lines(state, [rs.Shop(cpu.id, pid, state.week, 0.1)], know, seed=3)
    line = after.chat[-1]

    assert (line.kind, line.team_id, line.to_team, line.player_ids) == ("shop", cpu.id, rs.user_team(state).id, (pid,))
    assert know.by_id[pid].name in line.text


def _traded(state: rs.State, first: rs.Team, second: rs.Team, offer_id: int = 99) -> rs.State:
    deal = rs.Offer(offer_id, state.week, first.id, second.id, (first.roster[0],), (second.roster[0],), "accepted")
    return replace(state, offers=(*state.offers, deal))


def test_a_third_manager_reacts_to_trades_at_most_twice_a_week():
    state = start()
    cpus = [t for t in state.teams if not t.is_user]
    reacted = []
    for seed in range(20):
        traded = _traded(_traded(_traded(state, cpus[0], cpus[1], 90), cpus[2], cpus[3], 91), cpus[4], cpus[5], 92)
        after = chat.trade_reactions(traded, [90, 91, 92], knowledge(state), seed)
        lines = [c for c in after.chat if c.kind == "trade_reaction"]
        assert len(lines) <= chat.MAX_REACTIONS
        reacted.extend(lines)
        for line in lines:
            deal = next(o for o in after.offers if set(line.player_ids) <= {*o.give, *o.get})
            assert line.team_id not in (deal.from_team, deal.to_team)
    assert reacted


def test_every_new_offer_to_the_user_gets_a_message_in_its_senders_voice():
    state = start()
    user = rs.user_team(state)
    cpu = next(t for t in state.teams if not t.is_user)
    offer = rs.Offer(50, state.week, cpu.id, user.id, (cpu.roster[1],), (user.roster[1],), "pending")
    countered = replace(offer, id=51, counter_to=40)
    after = chat.label_offers(replace(state, offers=(offer, countered)), knowledge(state), seed=1)
    first, second = after.offers

    assert first.message and second.message and first.message != second.message
    assert second.message in chat.bank()["offer_counter"][cpu.persona] or second.message.startswith(
        tuple(line.split("{")[0] for line in chat.bank()["offer_counter"][cpu.persona]))
    assert chat.label_offers(after, knowledge(state), seed=1) == after              # labelled once
    assert all(c.kind.startswith("offer_") for c in after.chat) and not chat.chat_for(after)


def test_a_shopped_player_in_an_offer_reads_as_worry():
    state = start()
    user = rs.user_team(state)
    cpu = next(t for t in state.teams if not t.is_user)
    pid = cpu.roster[0]
    state = replace(state, shops=(rs.Shop(cpu.id, pid, state.week, 0.1),))
    offer = rs.Offer(60, state.week, cpu.id, user.id, (pid,), (user.roster[1],), "pending")
    after = chat.label_offers(replace(state, offers=(offer,)), knowledge(state), seed=2)

    assert after.chat[-1].kind == "offer_worried"
    assert knowledge(state).by_id[pid].name in after.offers[0].message


def test_an_old_save_without_chat_loads():
    state = season(0)[2]
    saved = rs.to_json(state)
    stripped = saved.replace(',"shops":[]', "")
    old = stripped[:stripped.index(',"chat":')] + "}"

    loaded = rs.state_from_json(old)
    assert loaded.chat == () and loaded.shops == ()
    assert rs.state_from_json(saved) == state


@pytest.mark.parametrize("week", [4, 8])
def test_chat_never_sees_the_future(week):
    played = season(0)[week - 1]                        # week `week` is scored; lines post before week + 1
    changed = fakes.future_changed(DATA, after_week=played.week)
    before = replace(played, chat=tuple(c for c in played.chat if c.week < week))
    now = chat.after_week(before, SETTINGS, knowledge(played), 0)
    later = chat.after_week(before, SETTINGS, knowledge(played, changed), 0)

    assert rs.to_json(now) == rs.to_json(later)
