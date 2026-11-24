"""Replay League chat, moods and shops over whole games (replay_game): a shopped player is the user's to buy
while his shop is open, what a manager says agrees in sign with his price, and nothing posted ever depends on
a week not yet played (the game-level proof, with replay_fakes.future_changed)."""
from collections.abc import Callable
from functools import lru_cache

import pytest

from services import replay_game as rg, replay_personas, replay_state as rs
from services.replay_pool import SeasonData
from tests import replay_fakes as fakes

SEED = 1
MODES = ("wednesday", "none")
DEADLINE = 12


def settings(mode: str) -> rs.Settings:
    return fakes.fake_settings(trade_deadline_week=DEADLINE, waiver_mode=mode)


def data_for(mode: str) -> SeasonData:
    chosen = settings(mode)
    return fakes.fake_season(chosen.season, chosen.scoring)


def play(mode: str, data: SeasonData, seed: int = SEED,
         stop: Callable[[rs.State], bool] = lambda state: False) -> tuple[rs.State, ...]:
    """Every state of a league with auto drafts and default lineups, from the draft's end to `stop` or the end."""
    chosen = settings(mode)
    state = rg.auto_draft(rg.create_game(chosen, data, seed), chosen, data, seed)
    states = [state]
    while state.phase != "complete" and not stop(state):
        state = rg.advance(state, chosen, data, seed, rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY)
        states.append(state)
    return tuple(states)


@lru_cache(maxsize=8)
def game(mode: str, seed: int = SEED) -> tuple[rs.State, ...]:
    return play(mode, data_for(mode), seed)


def shop_weeks(states: tuple[rs.State, ...]) -> set[tuple[int, int]]:
    """(player id, week) for every week a player was being shopped: from the states and the shop messages."""
    found = {(shop.player_id, shop.week) for state in states for shop in state.shops}
    return found | {(pid, line.week) for line in states[-1].chat if line.kind == "shop" for pid in line.player_ids}


# ---------------- shops ----------------

@pytest.mark.parametrize("mode", MODES)
def test_a_shopped_player_is_never_sold_to_another_cpu_while_his_shop_is_open(mode):
    states = game(mode)
    user = rs.user_team(states[0]).id
    shopped = shop_weeks(states)
    cpu_deals = [o for o in states[-1].offers if o.status == "accepted" and user not in (o.from_team, o.to_team)]
    sold = [(pid, o.week) for o in cpu_deals for pid in (*o.give, *o.get) if (pid, o.week) in shopped]
    offered = [o for o in states[-1].offers if o.to_team == user and any((pid, o.week) in shopped for pid in o.give)]

    assert shopped
    assert sold == []
    assert offered                                          # the shopping manager's offer goes to the user


@pytest.mark.parametrize("mode", MODES)
def test_every_open_shop_is_live_tradable_and_on_his_active_roster(mode):
    chosen = settings(mode)
    for state in game(mode):
        for shop in state.shops:
            assert shop.week == state.week and rs.trades_open(state, chosen)
            assert shop.player_id in rs.team(state, shop.team_id).roster
            assert not rs.retrade_locked(state, shop.player_id)


# ---------------- words agree with prices ----------------

def _lean(state: rs.State, mode: str, team_id: int, pid: int) -> float:
    """His private value of the player minus the chart, in the week the line was posted."""
    know = rg.knowledge(state, settings(mode), data_for(mode), seed=SEED)
    team, value = rs.team(state, team_id), know.by_id[pid]
    return replay_personas.private_value(value, team.persona, team.hype) - value.chart


@pytest.mark.parametrize("mode", MODES)
def test_offer_messages_and_shop_lines_agree_with_the_price(mode):
    states = game(mode)
    at_week = {state.week: state for state in states if state.phase in ("waivers", "open")}
    checked = 0
    for line in states[-1].chat:
        if line.kind not in ("offer_worried", "offer_high", "shop") or not line.player_ids:
            continue
        lean = _lean(at_week[line.week], mode, line.team_id, line.player_ids[0])
        assert lean >= -1e-9 if line.kind == "offer_high" else lean <= 1e-9, (line.kind, line.text, lean)
        checked += 1
    assert checked


# ---------------- no hindsight, the whole game ----------------

@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("week", [5, 9])
def test_a_whole_game_never_sees_the_future(mode, week):
    """Two leagues whose seasons differ only after `week + 1`'s pre-game news play identically, chat, moods and
    shops included, through week `week` and the start of the next."""
    def through(state: rs.State) -> bool:
        return state.phase == "open" and state.week == week + 1

    changed = play(mode, fakes.future_changed(data_for(mode), after_week=week + 1), stop=through)
    original = game(mode)[:len(changed)]

    assert through(changed[-1]) and changed[-1].chat
    for now, later in zip(original, changed, strict=True):
        assert now.chat == later.chat and now.shops == later.shops
        assert rs.to_json(now) == rs.to_json(later)

