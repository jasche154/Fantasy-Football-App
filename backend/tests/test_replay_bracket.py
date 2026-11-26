"""Replay League playoffs: bracket sizes 4-8 with byes and reseeding, third place, the pools, final places,
and old saves made before playoff sizes existed."""
import json
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest

from services import replay_season as season, replay_state as rs
from services.replay_state import Settings, State
from tests import replay_fakes as fakes

SEED = 7
REGULAR = 2                                  # a short regular season: seeds come from two real weeks
OLD_SAVES = Path(__file__).parent / "data" / "replay_pre_bracket_saves.json"

# Scripted playoffs (seed numbers, 1 = best): in the title bracket the lower seed wins every game before the
# third-place game, which is tied (so the higher seed takes it); every pool game is tied too.
# P: (title games week by week, byes, final places 1..P)
BRACKETS = {
    4: ([((1, 4), (2, 3)), ((3, 4), (1, 2))], (), (4, 3, 1, 2)),
    5: ([((4, 5),), ((1, 5), (2, 3)), ((3, 5), (1, 2))], (1, 2, 3), (5, 3, 1, 2, 4)),
    6: ([((3, 6), (4, 5)), ((1, 6), (2, 5)), ((5, 6), (1, 2))], (1, 2), (6, 5, 1, 2, 3, 4)),
    7: ([((2, 7), (3, 6), (4, 5)), ((1, 7), (5, 6)), ((6, 7), (1, 5))], (1,), (7, 6, 1, 5, 2, 3, 4)),
    8: ([((1, 8), (2, 7), (3, 6), (4, 5)), ((5, 8), (6, 7)), ((7, 8), (5, 6))], (), (8, 7, 5, 6, 1, 2, 3, 4)),
}
CASES = [(4, 8), (5, 10), (6, 12), (7, 12), (8, 8), (8, 14)]      # (playoff teams, league size)


def _settings(teams: int, playoff_teams: int) -> Settings:
    return fakes.fake_settings(teams=teams, playoff_teams=playoff_teams, regular_season_weeks=REGULAR,
                               trade_deadline_week=1)


def _week(state: State, settings: Settings, per_team: dict[int, float]) -> State:
    """Play the current week with every rostered skill player scoring his team's number (default 1.0); kickers and
    defenses score 0: the fake schedule has real byes in these weeks (LV, CLE, CIN in week 5), and a lone K or DST on
    a bye sits (replay_season.starters_for), which would change the scripted totals."""
    data = fakes.fake_season()
    actual = {(pid, state.week): 0.0 if data.players[pid].position in ("K", "DST") else per_team.get(team.id, 1.0)
              for team in state.teams for pid in team.roster}
    know = fakes.fake_knowledge(state.week, settings, data)
    return season.play_week(replace(state, phase="open"), settings, fakes.patched(data, actual=actual), know, SEED)


@lru_cache(maxsize=None)
def _regular(teams: int, playoff_teams: int) -> State:
    settings = _settings(teams, playoff_teams)
    state = fakes.drafted_state(settings)
    for _ in range(REGULAR):
        know = fakes.fake_knowledge(state.week, settings)
        state = season.play_week(replace(state, phase="open"), settings, fakes.fake_season(), know, SEED)
    return state


def _scripted(teams: int, playoff_teams: int) -> list[State]:
    """[after the regular season, after each playoff week] with the upsets described above BRACKETS."""
    settings, states = _settings(teams, playoff_teams), [_regular(teams, playoff_teams)]
    while states[-1].phase != "complete":
        state = states[-1]
        titles = season.title_games(state, settings, state.week)
        final_week = state.week == settings.end_week
        underdogs = [low for _, low in (titles[:1] if final_week else titles)]
        states.append(_week(state, settings, dict.fromkeys(underdogs, 3.0)))
    return states


def _idle(state: State, settings: Settings, week: int) -> set[int]:
    playing = [t for game in season.matchups_for_week(state, settings, week) for t in game]
    assert len(playing) == len(set(playing)), f"a team plays twice in week {week}"
    return {team.id for team in state.teams} - set(playing)


# ---------------- sizes and rounds ----------------

@pytest.mark.parametrize("playoff_teams, teams", CASES)
def test_scripted_bracket_pairs_byes_reseeds_and_places(playoff_teams, teams):
    settings = _settings(teams, playoff_teams)
    states = _scripted(teams, playoff_teams)
    s = season.playoff_seeds(states[0])
    seeded = lambda games: tuple((s[a - 1], s[b - 1]) for a, b in games)    # noqa: E731
    rounds, bye_seeds, places = BRACKETS[playoff_teams]

    done = states[-1]

    assert len(states) - 1 == rs.playoff_rounds(playoff_teams) == len(rounds)
    for index, games in enumerate(rounds):
        week = REGULAR + 1 + index
        before = states[index]
        assert season.title_games(before, settings, week) == seeded(games)
        assert season.title_games(done, settings, week) == seeded(games)           # the same once played
        assert season.matchups_for_week(done, settings, week)[:len(games)] == seeded(games)
        assert season.byes(before, settings, week) == (tuple(s[b - 1] for b in bye_seeds) if index == 0 else ())
    table = season.final_standings(done, settings)
    order = [row["team_id"] for row in table]
    assert order[:playoff_teams] == [s[seed - 1] for seed in places]
    assert sorted(order) == list(range(1, teams + 1)) and [row["place"] for row in table] == list(range(1, teams + 1))
    assert set(order[playoff_teams:]) == set(s[playoff_teams:])
    assert done.champion == order[0] == s[places[0] - 1]


@pytest.mark.parametrize("playoff_teams, teams", CASES)
def test_every_team_plays_once_a_week_unless_it_has_a_bye_or_is_a_pools_odd_one_out(playoff_teams, teams):
    settings, states = _settings(teams, playoff_teams), _scripted(teams, playoff_teams)
    rounds = rs.playoff_rounds(playoff_teams)
    odd_out = lambda size: size % 2                                             # noqa: E731

    for index in range(rounds):
        week, before = REGULAR + 1 + index, states[index]
        idle = _idle(before, settings, week)
        expected = len(season.byes(before, settings, week)) + odd_out(teams - playoff_teams)
        if rounds == 3 and index > 0:
            expected += odd_out(playoff_teams - 4)                              # the round-one losers' pool
        assert len(idle) == expected, f"week {week}"
        assert set(season.byes(before, settings, week)) <= idle
        assert _idle(states[-1], settings, week) == idle                        # played as listed


def test_labels_name_each_round():
    settings, states = _settings(12, 6), _scripted(12, 6)
    done = states[-1]
    labels = {week: [season.game_label(done, settings, week, game) for game in done.results[week - 1].matchups]
              for week in (3, 4, 5)}

    assert labels[3] == ["Quarter-final"] * 2 + ["Consolation"] * 3
    assert labels[4] == ["Semi-final"] * 2 + ["Consolation"] * 4                # 2 losers + 6 outside
    assert labels[5] == ["Championship", "Third place"] + ["Consolation"] * 4
    assert season.game_label(done, settings, 2, done.results[1].matchups[0]) is None
    four = _settings(8, 4)
    assert season.game_label(_regular(8, 4), four, 3, season.title_games(_regular(8, 4), four, 3)[0]) == "Semi-final"


def test_the_bracket_event_names_the_round_and_the_byes():
    states = _scripted(12, 6)
    names = {t.id: t.name for t in states[0].teams}
    s = season.playoff_seeds(states[0])

    texts = [e.text for e in states[-1].events if e.kind == "playoffs"]

    assert texts[0] == (f"Quarter-finals: {names[s[2]]} v {names[s[5]]}, {names[s[3]]} v {names[s[4]]} "
                        f"(byes: {names[s[0]]}, {names[s[1]]})")
    assert texts[1].startswith("Semi-finals: ") and "bye" not in texts[1]
    assert texts[2] == f"Championship game: {names[s[4]]} v {names[s[5]]}" and len(texts) == 3


def test_a_single_round_one_game_is_a_wild_card_game():
    states = _scripted(8, 5)
    names = {t.id: t.name for t in states[0].teams}
    s = season.playoff_seeds(states[0])

    first = next(e.text for e in states[-1].events if e.kind == "playoffs")

    assert first == f"Wild-card game: {names[s[3]]} v {names[s[4]]} (byes: {names[s[0]]}, {names[s[1]]}, {names[s[2]]})"


def test_matchups_are_unknown_until_the_week_before_is_played():
    settings = _settings(12, 6)
    regular = _regular(12, 6)

    assert season.matchups_for_week(regular, settings, 4) == () == season.title_games(regular, settings, 4)
    assert season.byes(regular, settings, 4) == ()
    assert season.byes(fakes.drafted_state(settings), settings, 3) == ()                  # seeds not final
    assert season.matchups_for_week(fakes.drafted_state(settings), settings, 3) == ()


# ---------------- the pools ----------------

def test_a_pool_of_two_plays_every_week_and_a_split_goes_by_points_then_seed():
    settings = _settings(6, 4)                            # outside the playoffs: seeds 5 and 6, two weeks
    regular = _regular(6, 4)
    s = season.playoff_seeds(regular)
    five, six = s[4], s[5]

    by_points = _week(_week(regular, settings, {six: 5.0}), settings, {five: 2.0})     # 54 to 27
    by_seed = _week(_week(regular, settings, {six: 2.0}), settings, {five: 2.0})       # 27 each

    for done in (by_points, by_seed):
        assert all({five, six} in map(set, r.matchups) for r in done.results[REGULAR:])
    assert [row["team_id"] for row in season.final_standings(by_points, settings)][4:] == [six, five]
    assert [row["team_id"] for row in season.final_standings(by_seed, settings)][4:] == [five, six]


def test_a_pool_pairs_teams_by_wins_then_points_then_seed():
    settings = _settings(10, 6)                           # outside: seeds 7-10
    regular = _regular(10, 6)
    s = season.playoff_seeds(regular)
    a, b, c, d = s[6:]

    first = _week(regular, settings, {b: 4.0, d: 2.0})   # b beats a, d beats c: d 18 points, b 36
    second = season.matchups_for_week(first, settings, REGULAR + 2)

    assert ((a, b) in first.results[-1].matchups and (c, d) in first.results[-1].matchups)
    assert second[-2:] == ((b, d), (a, c))


def test_the_idle_team_in_an_odd_pool_rotates():
    settings, states = _settings(12, 7), _scripted(12, 7)        # losers' pool: 3 teams; consolation: 5 teams
    s = season.playoff_seeds(states[0])
    losers = set(s[1:4]) | set(s[4:7])                           # round-one losers come from seeds 2-7
    outside = set(s[7:])

    idle = {week: _idle(states[week - REGULAR - 1], settings, week) for week in range(REGULAR + 1, REGULAR + 4)}
    losers_idle = [idle[week] & losers - {s[0]} for week in (REGULAR + 2, REGULAR + 3)]
    outside_idle = [idle[week] & outside for week in idle]

    assert all(len(group) == 1 for group in losers_idle + outside_idle)
    assert losers_idle[0] != losers_idle[1]                       # not the same team both weeks
    assert len(set().union(*outside_idle)) == 3                  # three different teams over three weeks


# ---------------- old saves ----------------

def _old(key: str) -> tuple[Settings, State]:
    saved = json.loads(OLD_SAVES.read_text())
    return rs.settings_from_json(json.dumps(saved["settings"])), rs.state_from_json(json.dumps(saved[key]))


def test_an_old_save_in_its_playoffs_loads_finishes_and_places_everyone():
    settings, state = _old("mid_playoffs")
    s = season.playoff_seeds(state)
    played = state.results[-1]
    winners = [season._decide(game, played.scores, s)[0] for game in played.matchups[:2]]

    assert (settings.playoff_teams, settings.end_week, state.week, state.phase) == (4, 4, 4, "waivers")
    assert season.title_games(state, settings, 3) == ((s[0], s[3]), (s[1], s[2])) == played.matchups[:2]
    final_games = season.matchups_for_week(state, settings, 4)
    assert set(final_games[0]) == set(winners)

    done = _week(state, settings, {})

    assert done.phase == "complete" and done.champion in winners
    table = season.final_standings(done, settings)
    assert [row["place"] for row in table] == list(range(1, 9)) and table[0]["team_id"] == done.champion
    assert sorted(row["team_id"] for row in table) == list(range(1, 9))


def test_an_old_save_between_the_regular_season_and_the_playoffs_keeps_its_title_games_and_finishes():
    settings, state = _old("pre_playoffs")
    _, old_week = _old("mid_playoffs")                    # the same league one week on, played by the old code

    games = season.matchups_for_week(state, settings, 3)
    done = _week(_week(state, settings, {}), settings, {})

    assert (state.week, state.phase, len(state.results)) == (3, "waivers", 2)
    assert games[:2] == season.title_games(state, settings, 3) == old_week.results[-1].matchups[:2]
    # The consolation games may pair differently from the old code (pool table, not seeds 5v8 / 6v7): accepted.
    assert sorted(t for game in games[2:] for t in game) == sorted(season.playoff_seeds(state)[4:])
    table = season.final_standings(done, settings)
    assert done.phase == "complete" and table[0]["team_id"] == done.champion
    assert sorted(row["team_id"] for row in table) == list(range(1, 9)) and [row["place"] for row in table] == list(range(1, 9))


def test_a_finished_old_save_keeps_its_top_four_and_places_everyone():
    settings, state = _old("complete")
    final = state.results[-1]

    table = season.final_standings(state, settings)

    order = [row["team_id"] for row in table]
    assert state.phase == "complete" and order[0] == state.champion
    assert set(order[:2]) == set(final.matchups[0]) and set(order[2:4]) == set(final.matchups[1])
    assert sorted(order[4:]) == sorted(season.playoff_seeds(state)[4:])
    assert [season.game_label(state, settings, 4, game) for game in final.matchups] == \
        ["Championship", "Third place", "Consolation", "Consolation"]
