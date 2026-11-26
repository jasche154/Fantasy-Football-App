"""Replay League "jump in now": a league drafted in week K (Settings.start_week) that plays weeks K to the end.

Built on the fake season: K = 8 (12 teams, weekly waivers) and K = 12 (4 playoff teams, FAAB, weeks 12-15
then playoffs 16-17). The draft knows exactly what was known before week K (`future_changed` proves it), the
league's schedule, results, standings and views hold only weeks K on, and a player's page still shows the real
NFL weeks before K. `market` stands in for replay_pool's current-market ADP: the pool repriced at week K.
"""
import contextlib
import json
from dataclasses import replace
from functools import lru_cache
from types import MappingProxyType

import pytest

from services import (
    replay_chart, replay_draft, replay_game as rg, replay_season, replay_state as rs, replay_values as rv, replay_views as views,
    replay_views_league as league,
)
from tests import replay_fakes as fakes
from tests.test_replay_game import problems

DATA = fakes.fake_season()
HURT_ID = fakes.player_id("RB", 1)      # the preseason RB1, on IR for the rest of the season from week 7
HURT_FROM = 7
_LATE = range(HURT_FROM, DATA.last_week + 1)
# STALE: tagged IR but his old projection still stands; HURT: no projection, no game and no expert rank either.
STALE = fakes.tagged(DATA, {(HURT_ID, w): fakes.Injury("IR") for w in _LATE})
HURT = replace(fakes.patched(STALE, projected={(HURT_ID, w): None for w in _LATE},
                             actual={(HURT_ID, w): None for w in _LATE}),
               expert_ranks=MappingProxyType({k: v for k, v in DATA.expert_ranks.items()
                                              if k[0] != HURT_ID or k[1] < HURT_FROM}))
K8 = fakes.fake_settings(start_week=8, draft_slot=4)
K12 = fakes.fake_settings(start_week=12, regular_season_weeks=15, playoff_teams=4, trade_deadline_week=13,
                          waiver_mode="faab", draft_slot=9)
SEED = 5


def market(data, week: int, settings: rs.Settings = K8):
    """The pool repriced on what was known before `week`: skill players ranked by that week's chart value
    (HURT_ID, out for the season, dropped), K and DST as they were."""
    know = rv.knowledge(data, rv.league_settings(settings), week, settings.end_week)
    skill = [v for v in know.values if v.position in fakes.SKILL and v.player_id != HURT_ID][:fakes.ADP_SKILL]
    adp = {v.player_id: float(n) for n, v in enumerate(skill, start=1)}
    players = {pid: replace(p, adp=adp.get(pid), adp_stdev=None) if p.position in fakes.SKILL else p
               for pid, p in data.players.items()}
    return replace(data, players=MappingProxyType(players))


MARKET8 = market(HURT, 8)
MARKET12 = market(DATA, 12, K12)


@lru_cache(maxsize=None)
def drafted(settings: rs.Settings, data=MARKET8) -> rs.State:
    return rg.auto_draft(rg.create_game(settings, data, SEED), settings, data, SEED)


@lru_cache(maxsize=None)
def season(settings: rs.Settings, data=MARKET8) -> tuple[rs.State, ...]:
    """Every state from the end of the draft to the champion, each checked against the game's invariants."""
    states = [drafted(settings, data)]
    while states[-1].phase != "complete":
        state = states[-1]
        step = rg.WEDNESDAY if state.phase == "waivers" else rg.PLAY
        after = rg.advance(state, settings, data, SEED, step)
        played = after.week - settings.start_week + (after.phase == "complete")
        faults = [f for f in problems(after, settings, state) if f != "results do not match the week"]
        assert faults == [] and len(after.results) == played, (state.week, faults)
        states.append(after)
    return tuple(states)


def at(week: int, settings: rs.Settings = K8, data=MARKET8) -> rs.State:
    """The league just before `week` is played (phase "open")."""
    return next(s for s in season(settings, data) if s.week == week and s.phase == "open")


def game(state: rs.State, settings: rs.Settings = K8, data=MARKET8) -> views.Game:
    return views.Game(1, "Jump in", settings, state, data, "2026-01-01T00:00:00", SEED)


def overall(state: rs.State, player_id: int) -> int | None:
    return next((pick.overall for pick in state.picks if pick.player_id == player_id), None)


# ---------------- settings ----------------

@pytest.mark.parametrize("overrides, message", [
    ({"start_week": 0}, "before week 1"),
    ({"start_week": 14}, "at least 2 regular-season weeks"),
    ({"start_week": 12, "trade_deadline_week": 11}, "can't be before the first week"),
    ({"sim_from": 0}, "simulated week"),
])
def test_settings_refuse_a_start_week_that_leaves_no_season(overrides, message):
    with pytest.raises(ValueError, match=message):
        fakes.fake_settings(**overrides)


def test_settings_saved_before_start_week_existed_load_as_a_week_one_league():
    saved = {k: v for k, v in json.loads(rs.to_json(fakes.fake_settings())).items() if k not in ("start_week", "sim_from")}

    loaded = rs.settings_from_json(json.dumps(saved))

    assert (loaded.start_week, loaded.sim_from, rs.draft_week(loaded)) == (1, None, 0)
    assert loaded == fakes.fake_settings() and rs.draft_week(K8) == 8
    assert rs.settings_from_json(rs.to_json(K12)) == K12


def test_an_old_league_still_drafts_in_week_zero_and_plays_from_week_one():
    settings = fakes.fake_settings(draft_slot=4)
    old = rs.settings_from_json(json.dumps({k: v for k, v in json.loads(rs.to_json(settings)).items()
                                            if k not in ("start_week", "sim_from")}))

    states = [rg.create_game(s, DATA, SEED) for s in (settings, old)]
    after = [rg.advance(rg.auto_draft(s, settings, DATA, SEED), settings, DATA, SEED, "play") for s in states]

    assert states[0] == states[1] and states[0].week == 0
    assert after[0] == after[1] and after[0].results[0].week == 1 and len(after[0].schedule) == 14


# ---------------- the draft ----------------

def test_the_draft_is_held_in_the_start_week_on_what_was_known_before_it():
    state = rg.create_game(K8, MARKET8, SEED)
    board = {p.player_id for p in replay_draft.available(state, MARKET8)}
    know = views.Game(1, "x", K8, state, MARKET8, None, SEED).know

    assert (state.phase, state.week) == ("draft", 8)
    newcomer = fakes.player_id("K", 33)                      # a kicker first seen in week 9: not known yet ...
    assert newcomer not in board and newcomer not in know.by_id
    later = rg.create_game(K12, MARKET12, SEED)              # ... but on the board of a week-12 draft
    assert newcomer in {p.player_id for p in replay_draft.available(later, MARKET12)}
    assert know.week == 8 and know.by_id[HURT_ID].injury.tag == "IR"
    assert know.by_id[fakes.BREAKOUT_ID].games == 7 - sum(not DATA.points(fakes.BREAKOUT_ID, w) for w in range(1, 8))


def test_a_jump_in_draft_follows_the_current_market_not_the_preseason_adp():
    preseason, current = drafted(K8, HURT), drafted(K8)

    assert overall(preseason, fakes.BREAKOUT_ID) > 100 and overall(current, fakes.BREAKOUT_ID) <= 16   # 5-game form: a little slower
    assert overall(current, fakes.BUST_ID) > 60
    # On IR with no news: drafted at his chart (45% off his healthy value), not skipped as the market's ADP would
    know = views.Game(1, "x", K8, rg.create_game(K8, MARKET8, SEED), MARKET8, None, SEED).know
    pick = replay_chart.chart_pick(know.by_id[HURT_ID].chart)
    assert 12 < overall(current, HURT_ID) <= pick + replay_draft.REACH_LIMIT


def test_a_player_without_an_adp_who_is_ruled_out_sorts_after_every_healthy_one():
    data = market(STALE, 8)                                  # his week-8 projection still stands, under an IR tag
    state = rg.create_game(K8, data, SEED)

    board = [p.player_id for p in replay_draft.available(state, data)]

    healthy = [i for i, pid in enumerate(board) if data.players[pid].adp is None and data.projection(pid, 8) > 0
               and pid != HURT_ID]
    assert data.projection(HURT_ID, 8) > 15 and board.index(HURT_ID) > max(healthy)


def test_the_draft_never_sees_week_k_or_later():
    shaken = market(fakes.future_changed(HURT, after_week=8), 8)
    # same prices, so only what the draft might peek at differs: results of week 8 on, projections after it
    shaken = replace(shaken, players=MARKET8.players)

    assert drafted(K8, shaken) == drafted(K8)
    assert drafted(K8, shaken).week == 8


# ---------------- the season ----------------

@pytest.mark.parametrize("settings", [K8, K12])
def test_after_the_draft_the_league_is_in_its_start_week_with_only_its_own_weeks_scheduled(settings):
    data = MARKET8 if settings is K8 else MARKET12
    state = drafted(settings, data)
    ids = sorted(team.id for team in state.teams)

    assert (state.phase, state.week, state.results) == ("open", settings.start_week, ())
    assert len(state.schedule) == settings.regular_season_weeks - settings.start_week + 1
    assert all(sorted(t for game_ in week for t in game_) == ids for week in state.schedule)   # everyone, every week
    assert [row["wins"] + row["losses"] + row["ties"] for row in rs.standings(state)] == [0] * settings.teams
    assert replay_season.matchups_for_week(state, settings, settings.start_week) == state.schedule[0]
    with pytest.raises(ValueError, match=f"plays weeks {settings.start_week} to {settings.end_week}"):
        replay_season.matchups_for_week(state, settings, settings.start_week - 1)


@pytest.mark.parametrize("settings", [K8, K12])
def test_a_jump_in_league_plays_to_a_champion_counting_only_its_own_weeks(settings):
    data = MARKET8 if settings is K8 else MARKET12
    final = season(settings, data)[-1]
    regular = settings.regular_season_weeks - settings.start_week + 1

    assert final.phase == "complete" and final.champion is not None
    assert [r.week for r in final.results] == list(range(settings.start_week, settings.end_week + 1))
    assert all(row["wins"] + row["losses"] + row["ties"] == regular for row in rs.standings(final))
    assert sorted(row["place"] for row in replay_season.final_standings(final, settings)) == list(range(1, 13))
    assert {e.kind for e in final.events} >= {"draft", "result", "playoffs", "champion"}
    assert min(e.week for e in final.events) == settings.start_week


def test_a_faab_jump_in_league_spends_and_trades_only_inside_its_weeks():
    final = season(K12, MARKET12)[-1]

    assert all(o.week >= K12.start_week for o in final.offers)
    assert all(o.week <= K12.trade_deadline_week + 1 for o in final.offers if o.status == "accepted")
    assert all(0 <= spent <= rs.FAAB_BUDGET for spent in final.faab_spent.values())


def test_the_first_week_is_scored_without_hindsight():
    shaken = replace(market(fakes.future_changed(HURT, after_week=8), 8), players=MARKET8.players)
    state = drafted(K8, shaken)

    played = rg.advance(state, K8, shaken, SEED, "play")

    assert played.results[0] == season(K8)[1].results[0]


# ---------------- views ----------------

def test_views_show_only_league_weeks_and_answer_cleanly_for_earlier_ones():
    shown = game(at(10))

    assert [row["week"] for row in league.schedule(shown)["results"]] == list(range(8, K8.end_week + 1))
    for screen in (lambda: views.scoreboard(shown, 5), lambda: views.team(shown, week=7),
                   lambda: views.matchup(shown, week=1)):
        with pytest.raises(ValueError):
            screen()
    assert views.scoreboard(shown, 8)["played"] and views.team(shown, week=8)["played"]
    assert views.matchup(shown, 9)["played"] and not views.matchup(shown)["played"]
    assert all(row["wins"] + row["losses"] + row["ties"] == 2 for row in league.standings(shown)["results"])
    assert all(event["week"] >= 8 for event in league.activity(shown, limit=10_000)["results"])
    json.dumps(views.summary(shown))


def test_a_players_page_still_shows_the_real_weeks_before_the_league_started():
    shown = game(at(8))
    pid = fakes.BREAKOUT_ID

    weeks = league.player(shown, pid)["weeks"]

    assert [row["week"] for row in weeks] == list(range(1, 9))
    assert [row["points"] for row in weeks[:7]] == [DATA.actual.get((pid, w)) for w in range(1, 8)]
    assert [row["played"] for row in weeks] == [True] * 7 + [False]
    assert weeks[-1]["points"] is None


def test_tendencies_wait_for_three_league_weeks():
    def hints(week: int) -> set:
        return {ref["tendency"] for ref in views.summary(game(at(week)))["teams"]}

    assert hints(8) == hints(10) == {None}
    assert hints(11) != {None}


def test_the_draft_room_shows_the_start_weeks_knowledge():
    state = rg.create_game(K8, MARKET8, SEED)
    shown = game(state)

    room = views.draft(shown, limit=views.MAX_PAGE)

    assert room["is_user_turn"] and len(room["picks"]) == K8.draft_slot - 1
    ranks = [row["pre_rank"] for row in room["results"]]
    assert ranks == sorted(ranks) and HURT_ID not in {row["player"]["id"] for row in room["results"][:30]}
    assert views.summary(shown)["week"] == 8 and views.draft(game(drafted(K8)))["results"] == []


def test_routes_answer_a_week_before_the_start_with_422_and_play_on_from_it():
    from database.db import get_session
    from services import replay_pool
    from tests.test_replay_routes import API, _client

    for client in _client(DATA):
        with contextlib.closing(client.app.dependency_overrides[get_session]()) as sessions:
            session = next(sessions)
            data = replay_pool.load_season_data(session, K8.season, K8.scoring)
            state = rg.auto_draft(rg.create_game(K8, data, SEED), K8, data, SEED)
            game_id = rs.create(session, "Jump in", K8, state, SEED).id
        base = f"{API}/games/{game_id}"

        early = client.get(f"{base}/scoreboard", params={"week": 3})
        weeks = [row["week"] for row in client.get(f"{base}/schedule").json()["results"]]
        played = client.post(f"{base}/advance", json={"step": "play", "week": 8})

        assert early.status_code == 422 and "plays weeks 8 to 16" in early.json()["detail"]
        assert weeks == list(range(8, 17))
        assert played.status_code == 200 and played.json()["game"]["week"] == 9


@pytest.mark.parametrize("week", [8, 10])
def test_no_jump_in_view_changes_when_the_future_does(week):
    shaken = replace(market(fakes.future_changed(HURT, after_week=week), 8), players=MARKET8.players)
    state = at(week)

    def screens(data) -> str:
        shown = game(state, data=data)
        return json.dumps([views.summary(shown), views.team(shown), views.matchup(shown),
                           views.scoreboard(shown, week), league.standings(shown), league.schedule(shown),
                           league.player(shown, fakes.BREAKOUT_ID), league.players(shown, limit=50),
                           league.playoffs(shown)])

    assert screens(shaken) == screens(MARKET8)
