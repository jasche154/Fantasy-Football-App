"""Replay League chat, moods and shops in a league that jumped in mid-season (Settings.start_week) and in a
simulated league over HTTP: everything counts league weeks, never NFL weeks before the league started.

K8 (tests/test_replay_jump_in.py) drafts in week 8 and plays weeks 8-14, then playoffs 15-16."""
import json
from collections.abc import Iterator
from dataclasses import replace
from functools import lru_cache

import pytest
from sqlalchemy.orm import Session

from services import (
    replay_blob, replay_chat as chat, replay_game as rg, replay_personas, replay_state as rs,
    replay_trades as rt, replay_views as views, replay_views_league as league,
)
from database.models import ReplaySeasonData
from tests import replay_fakes as fakes
from tests.test_replay_jump_in import K8, MARKET8, SEED, season
from tests.test_replay_routes import get, post
from tests.test_replay_sim_leagues import (  # noqa: F401 (fixtures)
    LIVE, THROUGH, _app, _frozen, engine_stand_in, live_today,
)

RESULT_KINDS = {"blowout_win", "close_win", "close_loss", "blowout_loss", "boom", "bust", "upset",
                "streak_win", "streak_loss", "clinch", "eliminated", "champion", "runner_up"}
SHOP_K8 = replace(K8, trade_deadline_week=13)          # a later deadline, so shops have weeks to open


def league_weeks(week: int, settings: rs.Settings = K8) -> int:
    """League weeks played once NFL week `week` is scored."""
    return week - settings.start_week + 1


def final_lines() -> tuple[rs.State, tuple[rs.Chat, ...]]:
    final = season(K8)[-1]
    return final, final.chat


# ---------------- after the games ----------------

def test_a_jump_in_league_talks_about_its_own_weeks_only():
    final, lines = final_lines()
    posted = {c.week for c in lines if c.kind in RESULT_KINDS}

    assert final.phase == "complete" and final.champion is not None
    assert min(c.week for c in lines) == K8.start_week
    assert set(range(K8.start_week, K8.regular_season_weeks + 1)) <= posted       # the user's opponent always talks
    assert all(o.week >= K8.start_week for o in final.offers)
    last = [c for c in lines if c.week == K8.end_week]
    assert any(c.kind in ("champion", "runner_up") for c in last)


def test_streaks_and_upsets_wait_for_league_weeks():
    _, lines = final_lines()
    streaks = [c for c in lines if c.kind in ("streak_win", "streak_loss")]
    upsets = [c for c in lines if c.kind == "upset"]

    assert streaks and upsets
    assert all(league_weeks(c.week) >= chat.STREAK for c in streaks)
    assert all(league_weeks(c.week) >= chat.UPSET_FROM_WEEK for c in upsets)
    for line in streaks:                    # the streak he talks about is made of league games only
        state = next(s for s in season(K8) if s.results and s.results[-1].week == line.week)
        assert chat.STREAK <= abs(chat._streak(state, line.team_id)) <= len(state.results)


def test_clinching_counts_the_weeks_left_from_the_league_week_not_from_week_one():
    states = season(K8)
    first = next(s for s in states if len(s.results) == 1)
    regular_done = next(s for s in states if s.results and s.results[-1].week == K8.regular_season_weeks)
    rows = rs.standings(regular_done)

    assert chat._playoff_status(first, K8) == (set(), set())                   # one game in: nothing settled
    clinched, out = chat._playoff_status(regular_done, K8)
    assert clinched == {r["team_id"] for r in rows if r["rank"] <= K8.playoff_teams}
    assert out == {r["team_id"] for r in rows if r["rank"] > K8.playoff_teams}
    for line in (c for c in states[-1].chat if c.kind in ("clinch", "eliminated")):
        seeded = line.team_id in clinched
        assert seeded == (line.kind == "clinch")


def test_moods_and_tendencies_show_from_the_third_league_week():
    def moods(week: int) -> set:
        state = next(s for s in season(K8) if s.week == week and s.phase == "open")
        shown = views.Game(1, "x", K8, state, MARKET8, None, SEED)
        return {ref["mood"] for ref in views.summary(shown)["teams"]}

    assert moods(8) == moods(10) == {None}
    assert moods(11) != {None}


# ---------------- shops ----------------

@lru_cache(maxsize=1)
def shop_season() -> tuple[rs.State, ...]:
    state = rg.auto_draft(rg.create_game(SHOP_K8, MARKET8, SEED), SHOP_K8, MARKET8, SEED)
    states = [state]
    while state.phase != "complete":
        state = rg.advance(state, SHOP_K8, MARKET8, SEED, rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY)
        states.append(state)
    return tuple(states)


def test_shops_open_only_after_three_league_weeks():
    states = shop_season()
    opened = [s for s in states if s.shops]
    shop_lines = [c for c in states[-1].chat if c.kind == "shop"]

    assert opened and shop_lines
    assert all(len(s.results) >= replay_personas.TENDENCY_WEEKS for s in opened)
    assert all(c.week > SHOP_K8.start_week + replay_personas.TENDENCY_WEEKS - 1 for c in shop_lines)


def test_a_shopped_player_is_bought_at_his_discount_in_a_jump_in_league():
    state = next(s for s in shop_season() if s.shops and s.phase == "waivers")
    shop = state.shops[0]
    user = rs.user_team(state)
    know = rg.knowledge(state, SHOP_K8, MARKET8, seed=SEED)
    plain = replace(state, shops=())

    def verdict(on: rs.State, give: tuple[int, ...]) -> rt.Verdict:
        return rt.evaluate(on, SHOP_K8, know, user.id, shop.team_id, give, (shop.player_id,))

    mine = sorted(user.roster, key=lambda pid: (know.by_id[pid].chart, pid))
    give = next((pid,) for pid in mine if verdict(state, (pid,)).accepted)
    assert verdict(state, give).score > verdict(plain, give).score               # the markdown is real
    view = league.trades(views.Game(1, "x", SHOP_K8, state, MARKET8, None, SEED))
    assert [row["player"]["id"] for row in view["shops"] if row["team"]["team_id"] == shop.team_id] == [shop.player_id]

    after, accepted, _ = rg.propose_trade(state, SHOP_K8, MARKET8, SEED, shop.team_id, give, (shop.player_id,))

    assert accepted.accepted and shop.player_id in rs.user_team(after).roster
    assert rs.markdowns(after, shop.team_id) == {}
    assert rs.state_from_json(rs.to_json(after)) == after


# ---------------- saves from either branch ----------------

def test_saves_from_before_either_feature_and_from_each_one_load():
    state = season(K8)[3]
    saved = json.loads(rs.to_json(state))
    settings = json.loads(rs.to_json(K8))
    no_chat = {k: v for k, v in saved.items() if k not in ("chat", "shops")}
    no_start = {k: v for k, v in settings.items() if k not in ("start_week", "sim_from")}

    sim_branch = rs.state_from_json(json.dumps(no_chat))                  # start_week settings, no chat or shops
    chat_branch = rs.settings_from_json(json.dumps(no_start))             # chat in the state, no start_week

    assert state.chat and sim_branch.chat == () and sim_branch.shops == ()
    assert rs.settings_from_json(json.dumps(settings)) == K8
    assert (chat_branch.start_week, chat_branch.sim_from) == (1, None)
    assert rs.state_from_json(json.dumps(saved)) == state
    played = rg.advance(sim_branch, K8, MARKET8, SEED, rg.WEDNESDAY if sim_branch.phase == "waivers" else rg.PLAY)
    assert played.chat or played.phase == "waivers"


# ---------------- a simulated league over HTTP ----------------

@pytest.fixture(scope="module")
def app() -> Iterator:
    yield from _app("sqlite://")


def test_a_simulated_now_league_has_chat_and_trades_from_its_start_week(app):
    client, engine = app
    game = post(client, "/games", {"season": LIVE, "seed": 6, "start": "now"})["game"]
    path, start = f"/games/{game['id']}", game["start_week"]
    post(client, path + "/draft/auto", whole_draft="true")
    drafted = get(client, path + "/chat", limit=200)["results"]          # maybe a reaction to a CPU trade
    assert start == THROUGH + 1 and all(row["week"] == start for row in drafted)

    for _ in range(4):
        summary = get(client, path)
        if summary["phase"] == "waivers":
            post(client, path + "/advance", {"step": "wednesday", "week": summary["week"]})
        post(client, path + "/advance", {"step": "play", "week": summary["week"]})

    summary, lines, trades = get(client, path), get(client, path + "/chat", limit=200), get(client, path + "/trades")
    assert summary["kind"] == "live" and summary["chat"] == lines["total"] > 0
    assert all(row["week"] >= start for row in lines["results"])
    assert {row["week"] for row in lines["results"]} >= set(range(start, start + 4))
    assert all(not row["team"]["is_user"] and "{" not in row["text"] for row in lines["results"])
    assert isinstance(trades["shops"], list) and all(o["message"] for o in trades["offers"])

    data = _frozen(engine, path)                       # the same screens on another future
    with Session(engine) as session:
        session.get(ReplaySeasonData, game["id"]).blob = replay_blob.dumps(
            fakes.future_changed(data, after_week=summary["week"]))
        session.commit()
    replay_blob.clear_cache()
    assert get(client, path + "/chat", limit=200) == lines and get(client, path + "/trades") == trades
