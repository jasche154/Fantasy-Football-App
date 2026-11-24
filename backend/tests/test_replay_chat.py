"""Replay League: the league chat (services/replay_chat.py): who talks after a week, the caps, no repeats,
trade reactions, offer messages, old saves and the no-hindsight rule."""
import json
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType

import pytest

from services import replay_chat as chat, replay_personas, replay_season, replay_state as rs, replay_values as rv
from tests import replay_fakes as fakes

SETTINGS = fakes.fake_settings(waiver_mode="none")
DATA = fakes.fake_season()


def knowledge(state: rs.State, data=DATA) -> rv.Knowledge:
    return rv.knowledge(data.as_of(state.week), rv.league_settings(SETTINGS), state.week, SETTINGS.end_week)


def talk(state: rs.State, seed: int, data=DATA) -> rs.State:
    """after_week as replay_game calls it, once `state.results[-1]` is scored."""
    return chat.after_week(state, SETTINGS, knowledge(state, data), seed, data.as_of(state.results[-1].week + 1))


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
        state = talk(state, seed)
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
            # nothing crowds him out, not even a week full of rare news (his own included)
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
                close, blowout, _ = chat.thresholds(state)
                assert margin < close if line.kind.startswith("close") else margin >= blowout


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


def test_an_old_save_without_chat_shops_or_offer_messages_loads():
    state = season(0)[2]
    cpus = [t for t in state.teams if not t.is_user]
    state = replace(_traded(state, cpus[0], cpus[1]), shops=(rs.Shop(cpus[2].id, cpus[2].roster[0], state.week, 0.1),))
    saved = json.loads(rs.to_json(state))
    old = {key: value for key, value in saved.items() if key not in ("chat", "shops")}
    old["offers"] = [{key: value for key, value in offer.items() if key != "message"} for offer in saved["offers"]]

    loaded = rs.state_from_json(json.dumps(old))
    assert loaded.chat == () and loaded.shops == () and [o.message for o in loaded.offers] == [""]
    assert loaded == replace(state, chat=(), shops=())
    assert rs.state_from_json(json.dumps(saved)) == state


@pytest.mark.parametrize("week", [4, 8])
def test_chat_never_sees_the_future(week):
    played = season(0)[week - 1]                        # week `week` is scored; lines post before week + 1
    changed = fakes.future_changed(DATA, after_week=played.week)
    before = replace(played, chat=tuple(c for c in played.chat if c.week < week))
    now = talk(before, 0)
    later = talk(before, 0, changed)

    assert rs.to_json(now) == rs.to_json(later)


# ---------------- the weekly cap ----------------

def candidates(state: rs.State, seed: int) -> list[chat.Candidate]:
    result = state.results[-1]
    before = replace(state, results=state.results[:-1], chat=())
    return chat._game_lines(state, SETTINGS, knowledge(state), DATA.as_of(result.week + 1), before, result, seed)


def test_the_cap_keeps_rare_news_first_and_shares_the_rest_fairly():
    firsts, lows = [], []
    for seed in range(4):
        for state in season(seed):
            found = candidates(state, seed)
            ranks = [rank for rank, *_ in found]
            assert ranks == sorted(ranks)
            opponent = next(a if b == user else b for a, b in state.results[-1].matchups
                            if (user := rs.user_team(state).id) in (a, b))
            assert [chat.priority(kind, team.id == opponent and kind in chat.TO_OPPONENT)
                    for _, team, kind, _, _ in found] == ranks
            rest = [team.id for rank, team, *_ in found if rank == chat.OTHER_PRIORITY]
            if len(rest) >= 2:
                firsts.append(rest[0])
                lows.append(min(rest))
            assert candidates(state, seed) == found             # seeded: a reload never re-orders
    assert firsts != lows                                       # not lowest team id first every time


def _talkative(monkeypatch) -> None:
    """Every CPU with something to say says it, so a week has more lines than the cap."""
    monkeypatch.setattr(chat, "OTHER_CHANCE", 1.0)
    monkeypatch.setattr(chat, "POST_CHANCE", dict.fromkeys(chat.POST_CHANCE, 1.0))


def test_rare_news_is_never_cut_for_a_plain_line(monkeypatch):
    states = [(seed, state) for seed in range(3) for state in season(seed)]     # built before the patch
    _talkative(monkeypatch)
    crowded = 0
    for seed, state in states:
        week = state.results[-1].week
        quiet = replace(state, chat=tuple(c for c in state.chat if c.week < week))
        posted = {(c.team_id, c.kind) for c in talk(quiet, seed).chat if c.week == week}
        ranks = {(team.id, kind): rank for rank, team, kind, _, _ in candidates(state, seed)}
        if len(ranks) <= chat.MAX_WEEK_LINES:
            continue
        crowded += 1
        assert len(posted) == chat.MAX_WEEK_LINES and posted <= set(ranks)
        assert max(ranks[p] for p in posted) <= min(rank for key, rank in ranks.items() if key not in posted)
    assert crowded >= 5


@pytest.mark.parametrize("news", ["clinch", "eliminated"])
def test_the_users_opponent_posts_his_playoff_news_as_well_as_his_line_to_the_user(monkeypatch, news):
    state = season(0)[7]
    week, user = state.results[-1].week, rs.user_team(state).id
    opponent = next(a if b == user else b for a, b in state.results[-1].matchups if user in (a, b))
    assert set(chat.bank()[news]) == set(chat.VOICES)               # every voice has the bucket
    _talkative(monkeypatch)
    newly = {"clinch": ({opponent}, set()), "eliminated": (set(), {opponent})}[news]
    monkeypatch.setattr(chat, "_playoff_status", lambda s, _: newly if s.results == state.results else (set(), set()))
    quiet = replace(state, chat=tuple(c for c in state.chat if c.week < week))
    posted = [c for c in talk(quiet, 0).chat if c.week == week]

    assert [(c.kind, c.to_team) for c in posted if c.team_id == opponent and c.kind == news] == [(news, None)]
    assert [c.team_id for c in posted if c.to_team == user] == [opponent]
    assert len(posted) <= chat.MAX_WEEK_LINES
    # his line to the user is kept ahead of everything, then the rare news: his own news never crowds it out
    assert (posted[0].team_id, posted[0].to_team) == (opponent, user)
    assert posted[1].kind == news


# ---------------- never mocking an injury ----------------

def _usage_patched(week: int, pid: int, snaps: float, data=DATA):
    usage = {**data.usage, (pid, week): replace(data.usage[(pid, week)], snap_pct=snaps)}
    return replace(data, usage=MappingProxyType(usage))


def test_a_player_who_left_his_game_early_is_never_a_bust_even_with_a_bye_next():
    week = 6
    state = replace(start(), week=week + 1)
    know = knowledge(state)
    pid = next(v.player_id for v in know.values if v.position == "WR" and (v.player_id, week) in DATA.usage
               and all((v.player_id, w) in DATA.usage for w in range(1, week)) and not v.out and v.injury is None)
    calm = replace(know, by_id=MappingProxyType({**know.by_id, pid: replace(know.by_id[pid], projection=0.0)}))
    norm = sorted(DATA.usage[(pid, w)].snap_pct for w in range(1, week))[week // 2]

    assert not chat.might_be_hurt(calm, _usage_patched(week, pid, norm).as_of(week + 1), pid, week)
    assert chat.might_be_hurt(calm, _usage_patched(week, pid, 0.3 * norm).as_of(week + 1), pid, week)
    tagged = fakes.tagged(_usage_patched(week, pid, norm), {(pid, week): fakes.Injury("Q")})
    assert chat.might_be_hurt(calm, tagged.as_of(week + 1), pid, week)
    assert chat.might_be_hurt(calm, _usage_patched(1, pid, norm).as_of(2), pid, 1)         # nothing to compare with
    hurt_next = replace(calm, by_id=MappingProxyType({**calm.by_id, pid: replace(calm.by_id[pid], out=True)}))
    assert chat.might_be_hurt(hurt_next, _usage_patched(week, pid, norm).as_of(week + 1), pid, week)


def test_every_bust_line_names_a_player_nobody_could_think_was_hurt():
    busts = 0
    for state in season(1):
        for line in (c for c in lines_of(state, state.results[-1].week) if c.kind == "bust"):
            busts += 1
            assert not chat.might_be_hurt(knowledge(state), DATA.as_of(line.week + 1), line.player_ids[0], line.week)
    assert busts


# ---------------- the final, long names, league scale ----------------

def test_a_tied_final_posts_no_champion_or_runner_up_margin_lines():
    final = season(0)[-1]
    result = final.results[-1]
    a, b = result.matchups[0]
    tied = replace(result, scores={**result.scores, b: result.scores[a]})
    state = replace(final, results=(*final.results[:-1], tied), chat=tuple(c for c in final.chat if c.week < result.week))
    after = talk(state, 0)

    assert not [c for c in after.chat[len(state.chat):] if c.kind in ("champion", "runner_up")]


def test_long_names_never_push_a_line_past_the_cap():
    state = start()
    team = replace(state.teams[1], name="The Extremely Long Team Name Of Destiny And Glory " * 2)
    values = {key: "Bartholomew Montgomery-Fitzgerald the Third" for key in chat.PLACEHOLDERS["upset"]}
    for _ in range(30):
        state = chat.post(state, team, "upset", {**values, "me": team.name}, seed=4)
    assert all(len(c.text) <= chat.MAX_CHARS for c in state.chat)
    assert state.chat == chat.post(state, team, "upset", {**values, "me": team.name}, seed=4).chat[:-1]
    plain = dict.fromkeys(chat.PLACEHOLDERS["upset"], "X")
    short = chat.post(start(), team, "upset", plain, seed=4).chat[-1]
    assert short.text == chat.bank()["upset"][team.persona][short.line].format_map(plain)   # nothing cut


def test_margins_and_booms_scale_with_the_leagues_scores():
    state = season(0)[6]
    doubled = replace(state, results=tuple(replace(r, scores={t: 2 * v for t, v in r.scores.items()})
                                           for r in state.results))

    assert chat.thresholds(doubled) == pytest.approx(tuple(2 * x for x in chat.thresholds(state)))
    close, blowout, boom = chat.thresholds(state)
    assert 8 < close < 12 and 20 < blowout < 30 and 20 < boom < 30         # a half-PPR league: about 10, 25, 25
