"""Replay League season: lineups, playing weeks with real points, playoffs, final standings."""
from dataclasses import replace
from functools import lru_cache

import pytest

from services import replay_season as season, replay_state as rs
from services.replay_state import Offer, Settings, State
from tests import replay_fakes as fakes

SEED = 7
SIZES = (8, 10, 12, 14)                 # with the default 4-team playoff
REGULAR, SEMIS, FINAL = 14, 15, 16      # the default settings: last regular-season week, then the playoffs


def _settings(teams: int = 12, **overrides) -> Settings:
    return fakes.fake_settings(teams=teams, **overrides)


def _play(state: State, settings: Settings, weeks: int, data=None) -> State:
    """Play up to `weeks` weeks, re-opening the league after each one as the orchestrator would."""
    data = data or fakes.fake_season()
    for _ in range(weeks):
        if state.phase == "complete":
            break
        know = fakes.fake_knowledge(state.week, settings, data)
        state = season.play_week(replace(state, phase="open"), settings, data, know, SEED)
    return state


@lru_cache(maxsize=None)
def _played(teams: int, weeks: int) -> State:
    """A league of that size after `weeks` weeks (16 = a finished season); states are frozen, so shared."""
    settings = _settings(teams)
    return _play(fakes.drafted_state(settings), settings, weeks)


def _week_with_points(state: State, settings: Settings, per_team: dict[int, float]) -> State:
    """Play the current week with every rostered player scoring his team's number in `per_team`."""
    data = fakes.fake_season()
    actual = {(pid, state.week): per_team[team.id] for team in state.teams for pid in team.roster}
    know = fakes.fake_knowledge(state.week, settings, data)
    return season.play_week(replace(state, phase="open"), settings, fakes.patched(data, actual=actual), know, SEED)


def _user_by_position(state: State, know) -> dict[str, list[int]]:
    """The user's roster by position, best projection first."""
    found: dict[str, list[int]] = {}
    for value in sorted((know.by_id[pid] for pid in rs.user_team(state).roster), key=lambda v: -v.projection):
        found.setdefault(value.position, []).append(value.player_id)
    return found


# ---------------- a whole season ----------------

@pytest.mark.parametrize("teams", SIZES)
def test_full_season_plays_through_to_a_champion_from_the_title_bracket(teams):
    settings = _settings(teams)

    done = _played(teams, FINAL)

    final = done.results[-1]
    assert (done.phase, done.week, len(done.results)) == ("complete", FINAL, FINAL)
    assert done.champion in season.playoff_seeds(done)[:4]
    assert final.scores[done.champion] >= max(final.scores[team] for team in final.matchups[0])
    assert [r.playoff for r in done.results] == [False] * REGULAR + [True, True]
    assert rs.state_from_json(rs.to_json(done)) == done
    assert season.matchup_for(done, settings, done.champion) == final.matchups[0]


@pytest.mark.parametrize("teams", SIZES)
def test_every_team_has_exactly_one_game_every_week_including_playoffs(teams):
    done = _played(teams, FINAL)

    for result in done.results:
        playing = sorted(team_id for game in result.matchups for team_id in game)
        assert playing == list(range(1, teams + 1)), f"week {result.week}"
        assert set(result.scores) == set(playing)


def test_scores_are_the_sum_of_each_teams_starters_real_points():
    data, done = fakes.fake_season(), _played(12, FINAL)

    for result in done.results:
        started = [pid for lineup in result.starters.values() for ids in lineup.values() for pid in ids]
        assert set(result.points) == set(started) and len(started) == len(set(started))
        assert all(result.points[pid] == data.points(pid, result.week) for pid in started)
        for team_id, lineup in result.starters.items():
            total = sum(data.points(pid, result.week) for ids in lineup.values() for pid in ids)
            assert result.scores[team_id] == pytest.approx(total, abs=0.005)
            sitting = {slot: n - len(lineup[slot]) for slot, n in _settings().starters.items() if len(lineup[slot]) < n}
            assert set(sitting) <= {"K", "DST"} and all(n == 1 for n in sitting.values())   # only a lone K/DST on bye


def test_a_lone_kicker_or_defense_on_a_bye_sits_and_his_slot_stays_empty():
    """Oracle: the auto-lineup started a defense on its bye."""
    data, state = fakes.fake_season(), _played(12, FINAL)
    bye = next((t, w) for t in state.teams for w in range(1, 13) for pid in t.roster
               if data.players[pid].position == "DST" and data.on_bye(pid, w))
    team, week = bye
    lineup = next(r.starters[team.id] for r in state.results if r.week == week)

    assert lineup["DST"] == () or all(not data.on_bye(pid, week) for pid in lineup["DST"])


def test_play_week_is_deterministic_and_leaves_its_input_alone():
    settings, data = _settings(), fakes.fake_season()
    start = fakes.drafted_state(settings)
    know = fakes.fake_knowledge(1, settings)

    first = season.play_week(start, settings, data, know, SEED)
    second = season.play_week(start, settings, data, know, SEED + 1)

    assert first == second and (start.week, start.results, start.events) == (1, (), ())   # nothing is random


# ---------------- standings ----------------

def test_standings_add_up_from_the_regular_season_results_only():
    regular, done = _played(12, REGULAR), _played(12, FINAL)

    table = rs.standings(regular)

    assert table == rs.standings(done)                           # playoff games don't count
    assert all(row["wins"] + row["losses"] + row["ties"] == REGULAR for row in table)
    assert sum(row["wins"] for row in table) == sum(row["losses"] for row in table)
    for row in table:
        scored = sum(r.scores[row["team_id"]] for r in regular.results)
        assert row["points_for"] == pytest.approx(scored, abs=0.01)
    keys = [(-(row["wins"] + row["ties"] / 2), -row["points_for"], row["team_id"]) for row in table]
    assert keys == sorted(keys)
    assert season.playoff_seeds(regular) == tuple(row["team_id"] for row in table)


def test_tied_regular_season_game_counts_as_a_tie_for_both_teams():
    settings = _settings()
    state = fakes.drafted_state(settings)

    played = _week_with_points(state, settings, dict.fromkeys(range(1, 13), 1.0))

    assert all(row["ties"] == 1 and row["wins"] == 0 for row in rs.standings(played))
    results = [e for e in played.events if e.kind == "result"]
    assert len(results) == 6 and all(e.team_id is None and "tied 9.00-9.00" in e.text for e in results)


def test_final_standings_are_the_final_the_third_place_game_then_the_consolation_pool():
    settings, done = _settings(), _played(12, FINAL)
    seeds, final = season.playoff_seeds(done), done.results[-1]

    table = season.final_standings(done, settings)

    order = [row["team_id"] for row in table]
    assert [row["place"] for row in table] == list(range(1, 13)) and order[0] == done.champion
    for index, game in enumerate(final.matchups[:2]):            # the final, then the third-place game
        assert order[2 * index:2 * index + 2] == sorted(game, key=lambda t: (-final.scores[t], seeds.index(t)))
    assert set(order[:4]) == set(seeds[:4]) and set(order[4:]) == set(seeds[4:])
    assert table[0] == {**next(row for row in rs.standings(done) if row["team_id"] == order[0]), "place": 1}
    before_final = season.final_standings(_played(12, SEMIS), settings)      # no final yet: season order
    assert tuple(row["team_id"] for row in before_final) == seeds


# ---------------- playoffs ----------------

@pytest.mark.parametrize("teams", SIZES)
def test_first_playoff_week_is_one_v_four_two_v_three_and_the_rest_paired_by_seed(teams):
    settings, regular = _settings(teams), _played(teams, REGULAR)
    s = season.playoff_seeds(regular)

    games = season.matchups_for_week(regular, settings, SEMIS)

    assert list(games) == [(s[0], s[3]), (s[1], s[2])] + [(s[i], s[i + 1]) for i in range(4, teams, 2)]
    assert (regular.phase, regular.week) == ("waivers", SEMIS)
    assert [e.week for e in regular.events if e.kind == "playoffs"] == [REGULAR]


@pytest.mark.parametrize("teams", SIZES)
def test_second_playoff_week_is_the_final_and_the_third_place_game(teams):
    done = _played(teams, FINAL)
    semis, final = done.results[-2], done.results[-1]
    seeds = season.playoff_seeds(done)

    decided = [max(game, key=lambda t: (semis.scores[t], -seeds.index(t))) for game in semis.matchups[:2]]
    assert final.matchups[0] == tuple(sorted(decided, key=seeds.index))
    assert set(final.matchups[1]) == set(seeds[:4]) - set(decided)
    assert {t for game in final.matchups[2:] for t in game} == set(seeds[4:])


def test_tied_playoff_games_go_to_the_higher_seed():
    settings, regular = _settings(), _played(12, REGULAR)
    s = season.playoff_seeds(regular)

    semis = _week_with_points(regular, settings, dict.fromkeys(range(1, 13), 1.0))
    done = _week_with_points(semis, settings, dict.fromkeys(range(1, 13), 1.0))

    assert season.matchups_for_week(semis, settings, FINAL)[:2] == ((s[0], s[1]), (s[2], s[3]))
    assert done.champion == s[0]
    assert [row["team_id"] for row in season.final_standings(done, settings)][:5] == list(s[:5])
    assert sum("as the higher seed" in e.text for e in done.events if e.kind == "result") == 12


def test_lower_seeds_that_win_their_semi_final_meet_in_the_final():
    settings, regular = _settings(), _played(12, REGULAR)
    s = season.playoff_seeds(regular)
    upsets = {**dict.fromkeys(range(1, 13), 1.0), s[3]: 3.0, s[2]: 3.0}

    semis = _week_with_points(regular, settings, upsets)
    done = _week_with_points(semis, settings, {**dict.fromkeys(range(1, 13), 1.0), s[3]: 2.0})

    assert season.matchups_for_week(semis, settings, FINAL)[:2] == ((s[2], s[3]), (s[0], s[1]))
    assert done.champion == s[3]
    assert [row["team_id"] for row in season.final_standings(done, settings)][:4] == [s[3], s[2], s[0], s[1]]
    champion_events = [e for e in done.events if e.kind == "champion"]
    assert len(champion_events) == 1 and champion_events[0].team_id == s[3]
    assert sum(e.kind == "playoffs" for e in done.events) == 2


def test_matchups_are_the_schedule_then_unknown_until_the_bracket_is_set():
    settings = _settings()
    state = fakes.drafted_state(settings)

    assert season.matchups_for_week(state, settings, 3) == state.schedule[2]
    assert season.matchups_for_week(state, settings, SEMIS) == ()
    assert season.matchups_for_week(_played(12, REGULAR), settings, FINAL) == ()
    assert season.matchup_for(state, settings, 1, SEMIS) is None and 1 in season.matchup_for(state, settings, 1)
    assert season.matchup_for(_played(12, 3), settings, 1, 2) in state.schedule[1]
    assert [season.is_playoff_week(settings, week) for week in (14, 15, 16, 17)] == [False, True, True, False]
    for week in (0, 17):
        with pytest.raises(ValueError, match="weeks 1 to 16"):
            season.matchups_for_week(state, settings, week)


# ---------------- CPU lineups ----------------

def test_cpus_bench_bye_and_out_players_whenever_someone_healthy_fits_the_slot():
    settings, done = _settings(), _played(12, FINAL)
    eligible = {"FLEX": ("RB", "WR", "TE")}
    zero_starts = benched = 0

    for result in done.results[4:12]:                            # the bye weeks, with the star out in 6-9
        know = fakes.fake_knowledge(result.week, settings)
        for team in done.teams:
            lineup = result.starters[team.id]
            starting = {pid for ids in lineup.values() for pid in ids}
            bench = [know.by_id[pid] for pid in team.roster if pid not in starting]
            benched += sum(v.projection == 0 and v.ros_ppg > 8 for v in bench)
            for slot, ids in lineup.items():
                for pid in (p for p in ids if know.by_id[p].projection == 0):
                    zero_starts += 1
                    assert not [v for v in bench if v.projection > 0 and v.position in eligible.get(slot, (slot,))]
    assert benched > 20 and zero_starts > 0                      # both cases really happen


def test_injured_star_starts_when_healthy_and_sits_while_out():
    done = _played(12, FINAL)
    star = fakes.INJURED_STAR_ID
    owner = next(team.id for team in done.teams if star in team.roster)

    started = {r.week for r in done.results if any(star in ids for ids in r.starters[owner].values())}

    assert {1, 10} <= started and not started & set(fakes.INJURED_WEEKS)


def _to_ir(state: State, team_id: int, player_id: int) -> State:
    team = rs.team(state, team_id)
    return rs.with_team(state, replace(team, roster=tuple(p for p in team.roster if p != player_id),
                                       ir=(*team.ir, player_id)))


def test_an_ir_player_never_starts_or_scores_and_starts_again_once_activated():
    settings, star = _settings(), fakes.INJURED_STAR_ID
    state = _play(fakes.drafted_state(settings), settings, 5)
    owner = next(team.id for team in state.teams if star in team.roster)
    state = _play(_to_ir(state, owner, star), settings, 4)              # weeks 6-9 with him on IR
    team = rs.team(state, owner)
    state = rs.with_team(state, replace(team, roster=(*team.roster, star), ir=()))
    state = _play(state, settings, 1)                                  # week 10: activated

    started = {r.week for r in state.results if any(star in ids for ids in r.starters[owner].values())}
    scored = {r.week for r in state.results if star in r.points}
    assert started == scored and {1, 10} <= started and not started & set(fakes.INJURED_WEEKS)


def test_a_player_on_ir_cant_be_started():
    settings = _settings()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    back = _user_by_position(state, know)["RB"][0]

    with pytest.raises(ValueError, match=f"^{know.by_id[back].name} is on IR: activate him before starting him.$"):
        season.set_lineup(_to_ir(state, 1, back), settings, know, {"RB": [back]})


def test_a_manual_starter_moved_to_ir_is_replaced_from_the_bench():
    settings = _settings()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    mine = _user_by_position(state, know)
    hurt, kept, next_best = mine["RB"][:3]
    lined_up = season.set_lineup(state, settings, know, {"RB": [hurt, kept]})

    repaired = season.starters_for(_to_ir(lined_up, 1, hurt), settings, know, 1)

    assert repaired["RB"] == (kept, next_best)
    played = season.play_week(_to_ir(lined_up, 1, hurt), settings, fakes.fake_season(), know, SEED)
    assert hurt not in played.results[0].points


# ---------------- the user's lineup ----------------

def test_manual_lineup_is_stored_used_and_cleared_after_the_week():
    settings, data = _settings(), fakes.fake_season()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    mine = _user_by_position(state, know)
    chosen = {"QB": [mine["QB"][1]], "RB": mine["RB"][3:5], "WR": mine["WR"][:2], "TE": mine["TE"][:1],
              "FLEX": [mine["WR"][4]], "K": mine["K"], "DST": mine["DST"]}

    lined_up = season.set_lineup(state, settings, know, chosen)
    played = season.play_week(lined_up, settings, data, know, SEED)

    expected = {slot: tuple(ids) for slot, ids in chosen.items()}
    assert state.lineup is None and dict(lined_up.lineup) == expected
    assert season.starters_for(lined_up, settings, know, 1) == expected
    assert season.starters_for(lined_up, settings, know, 1) != season.starters_for(state, settings, know, 1)
    assert season.starters_for(lined_up, settings, know, 2) == season.starters_for(state, settings, know, 2)
    assert dict(played.results[0].starters[1]) == expected and played.lineup is None
    assert season.clear_lineup(lined_up) == state


def test_empty_lineup_slots_score_zero():
    settings, data = _settings(), fakes.fake_season()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    quarterback = _user_by_position(state, know)["QB"][0]

    played = season.play_week(season.set_lineup(state, settings, know, {"QB": [quarterback]}), settings, data,
                              know, SEED)

    result = played.results[0]
    assert result.scores[1] == data.points(quarterback, 1) > 0
    assert result.starters[1]["RB"] == () and result.starters[1]["K"] == ()


def test_set_lineup_rejects_bad_lineups_with_a_clear_sentence():
    settings = _settings()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    mine = _user_by_position(state, know)
    stranger = state.teams[1].roster[0]
    bad = {
        "can't play in the QB slot": {"QB": mine["WR"][:1]},
        "can't play in the FLEX slot": {"FLEX": mine["QB"][:1]},
        "is not on your roster": {"RB": [stranger]},
        "Player 999 is not on your roster": {"RB": [999]},
        "is in the lineup twice": {"RB": mine["RB"][:1], "FLEX": mine["RB"][:1]},
        "The RB slot takes 2, not 3 players": {"RB": mine["RB"][:3]},
        "no SUPERFLEX slot": {"SUPERFLEX": mine["QB"][:1]},
    }

    for message, starters in bad.items():
        with pytest.raises(ValueError, match=message):
            season.set_lineup(state, settings, know, starters)
    for phase in ("draft", "complete"):
        with pytest.raises(ValueError, match="during the season"):
            season.set_lineup(replace(state, phase=phase), settings, know, {})
    assert season.set_lineup(replace(state, phase="waivers"), settings, know, {"SUPERFLEX": []}).lineup is not None


def test_lineup_is_repaired_when_a_starter_leaves_the_roster():
    settings = _settings()
    state, know = fakes.drafted_state(settings), fakes.fake_knowledge(1, settings)
    mine = _user_by_position(state, know)
    gone, kept, next_best = mine["RB"][:3]
    lined_up = season.set_lineup(state, settings, know, {"QB": mine["QB"][:1], "RB": [gone, kept],
                                                         "FLEX": [mine["WR"][0]]})

    after_drop = rs.release(lined_up, settings, 1, gone)
    repaired = season.starters_for(after_drop, settings, know, 1)

    assert repaired["RB"] == (kept, next_best)                   # refilled from the bench, best first
    assert repaired["FLEX"] == (mine["WR"][0],) and repaired["QB"] == (mine["QB"][0],)
    assert repaired["WR"] == () and repaired["K"] == ()          # left empty on purpose: still empty
    played = season.play_week(after_drop, settings, fakes.fake_season(), know, SEED)
    assert dict(played.results[0].starters[1]) == repaired


# ---------------- the end of a week ----------------

@pytest.mark.parametrize("mode, phase", [("wednesday", "waivers"), ("faab", "waivers"), ("none", "open")])
def test_week_ends_in_the_next_phase_with_offers_expired(mode, phase):
    settings, data = _settings(waiver_mode=mode), fakes.fake_season()
    offers = (Offer(1, 1, 2, 1, (5,), (6,), "pending"), Offer(2, 1, 3, 4, (7,), (8,), "accepted"),
              Offer(3, 1, 5, 1, (9,), (10,), "declined"))
    state = replace(fakes.drafted_state(settings), offers=offers, next_id=4)

    played = season.play_week(state, settings, data, fakes.fake_knowledge(1, settings), SEED)

    assert (played.phase, played.week, played.lineup) == (phase, 2, None)
    assert [o.status for o in played.offers] == ["expired", "accepted", "declined"]
    assert [e.id for e in played.events] == list(range(4, 10)) and played.next_id == 10
    assert all(e.kind == "result" and e.week == 1 for e in played.events)
    assert (played.claims, played.locked, played.teams) == (state.claims, state.locked, state.teams)
    if phase == "open":                                          # "none" mode: straight on to week 2
        assert season.play_week(played, settings, data, fakes.fake_knowledge(2, settings), SEED).week == 3


@pytest.mark.parametrize("phase", ["draft", "waivers", "complete"])
def test_play_week_refuses_any_phase_but_open(phase):
    settings = _settings()
    state = replace(fakes.drafted_state(settings), phase=phase)

    with pytest.raises(ValueError, match=f"'{phase}' phase"):
        season.play_week(state, settings, fakes.fake_season(), fakes.fake_knowledge(1, settings), SEED)


def test_play_week_refuses_the_wrong_weeks_knowledge_or_data_without_the_week():
    settings, data = _settings(), fakes.fake_season()
    state = fakes.drafted_state(settings)

    with pytest.raises(ValueError, match="known before week 2"):
        season.play_week(state, settings, data, fakes.fake_knowledge(2, settings), SEED)
    with pytest.raises(ValueError, match="needs the full season"):
        season.play_week(state, settings, data.as_of(1), fakes.fake_knowledge(1, settings), SEED)
    with pytest.raises(ValueError, match="has no week 1"):
        season.play_week(state, settings, replace(data, last_week=0), fakes.fake_knowledge(1, settings), SEED)
    with pytest.raises(ValueError, match="earlier week has not been played"):
        season.play_week(replace(state, week=SEMIS), settings, data, fakes.fake_knowledge(SEMIS, settings), SEED)


# ---------------- no hindsight ----------------

def test_starters_ignore_the_real_points_of_the_week_being_played():
    settings, data = _settings(), fakes.fake_season()
    state = replace(_played(12, 6), phase="open")                # week 7: byes and the star out
    know = fakes.fake_knowledge(7, settings)
    changed = fakes.future_changed(data, after_week=6)           # week 7's real points are different

    real = season.play_week(state, settings, data, know, SEED).results[-1]
    other = season.play_week(state, settings, changed, know, SEED).results[-1]

    assert other.starters == real.starters and other.matchups == real.matchups
    assert other.scores != real.scores


def test_season_through_week_seven_is_identical_when_only_the_future_differs():
    settings = _settings()
    changed = fakes.future_changed(fakes.fake_season(), after_week=7)

    played = _play(fakes.drafted_state(settings), settings, 7, changed)

    assert played == _played(12, 7)
