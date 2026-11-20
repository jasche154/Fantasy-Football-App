"""Replay League draft: setup, turn order, pick rules, and what simulated CPU drafts look like."""
from collections import Counter
from functools import lru_cache

import pytest

from services import replay_draft as rd, replay_state as rs, replay_values as rv
from services.replay_pool import SeasonData
from services.replay_state import Settings, State
from tests import replay_fakes as fakes

SEEDS = range(20)
SIZES = rs.VALID_TEAMS
DEFAULT_CAPS = {"QB": 2, "RB": 6, "WR": 7, "TE": 2, "K": 1, "DST": 1}
ADP_WINDOW = 100                    # the picks where a draft should still track ADP closely
MAX_MEAN_ADP_GAP = 8.0              # measured 4.2 to 5.2 picks on the fake season


def play_out(state: State, settings: Settings, data: SeasonData, seed: int) -> State:
    """Finish a draft with the user on autopick."""
    while state.phase == "draft":
        state = rd.run_cpu_picks(state, settings, data, seed)
        if state.phase == "draft":
            state = rd.autopick_user(state, settings, data, seed)
    return state


@lru_cache(maxsize=None)
def drafted(teams: int = 12, seed: int = 0, difficulty: str = "normal") -> State:
    settings = fakes.fake_settings(teams=teams, draft_slot=3, difficulty=difficulty)
    return play_out(rd.new_game(settings, seed), settings, fakes.fake_season(), seed)


def positions(state: State, team_id: int) -> list[str]:
    players = fakes.fake_season().players
    return [players[pid].position for pid in rs.team(state, team_id).roster]


def picks_with_players(state: State) -> list[tuple[rs.Pick, object]]:
    players = fakes.fake_season().players
    return [(pick, players[pick.player_id]) for pick in state.picks]


def mean_adp_gap(state: State) -> float:
    gaps = [abs(pick.overall - player.adp) for pick, player in picks_with_players(state)[:ADP_WINDOW]]
    return sum(gaps) / len(gaps)


# ---------------- setting up ----------------

@pytest.mark.parametrize("teams", SIZES)
def test_new_game_seats_the_user_at_the_draft_slot_among_named_cpu_teams(teams):
    settings = fakes.fake_settings(teams=teams, draft_slot=teams - 1)

    state = rd.new_game(settings, seed=5)

    assert (state.phase, state.week, state.picks, state.schedule) == ("draft", 0, (), ())
    assert [t.id for t in state.teams] == list(state.draft_order) == list(range(1, teams + 1))
    assert [t.id for t in state.teams if t.is_user] == [settings.draft_slot]
    assert rs.user_team(state).name == rd.USER_TEAM_NAME
    assert len({t.name for t in state.teams}) == teams
    assert all(t.name in rd.CPU_TEAM_NAMES and t.roster == () for t in state.teams if not t.is_user)


def test_cpu_managers_get_seeded_chaos_and_lean_and_the_user_gets_none():
    state = rd.new_game(fakes.fake_settings(teams=14), seed=9)
    cpus = [t for t in state.teams if not t.is_user]

    assert all(rd.CHAOS_RANGE[0] <= t.chaos <= rd.CHAOS_RANGE[1] for t in cpus)
    assert all(rd.LEAN_RANGE[0] <= lean <= rd.LEAN_RANGE[1] for t in cpus for lean in t.lean.values())
    assert all(set(t.lean) == set(DEFAULT_CAPS) for t in state.teams)
    assert len({t.chaos for t in cpus}) == len(cpus)
    assert rs.user_team(state).chaos == 0.0 and set(rs.user_team(state).lean.values()) == {1.0}


def test_new_game_is_set_by_the_seed_and_survives_json():
    settings = fakes.fake_settings()

    first, again, other = rd.new_game(settings, 1), rd.new_game(settings, 1), rd.new_game(settings, 2)

    assert first == again and first.teams != other.teams
    assert rs.state_from_json(rs.to_json(first)) == first


def test_new_game_takes_the_callers_team_names_or_rejects_the_wrong_number():
    settings = fakes.fake_settings(teams=8, draft_slot=2)
    names = [f"Club {n}" for n in range(1, 9)]

    state = rd.new_game(settings, 3, names)

    assert [t.name for t in state.teams] == names
    assert [t.chaos for t in state.teams] == [t.chaos for t in rd.new_game(settings, 3).teams]
    with pytest.raises(ValueError, match="8 team names"):
        rd.new_game(settings, 3, names[:7])


# ---------------- turn order and the board ----------------

def test_the_draft_snakes_and_nobody_is_on_the_clock_afterwards():
    settings, data = fakes.fake_settings(teams=8), fakes.fake_season()
    state, order = rd.new_game(settings, 0), []

    for _ in range(16):
        order.append(rd.on_the_clock(state, settings))
        state = rd.make_pick(state, settings, data, rd.cpu_pick(state, settings, data, 0), order[-1])

    assert order == [1, 2, 3, 4, 5, 6, 7, 8, 8, 7, 6, 5, 4, 3, 2, 1]
    assert [(p.overall, p.round) for p in state.picks[7:9]] == [(8, 1), (9, 2)]
    assert rd.on_the_clock(drafted(8), settings) is None


def test_available_lists_adp_order_then_players_without_adp_by_preseason_projection():
    settings, data = fakes.fake_settings(), fakes.fake_season()
    state = rd.new_game(settings, 0)

    board = rd.available(state, data)
    ranked, unranked = [p for p in board if p.adp is not None], [p for p in board if p.adp is None]

    assert len(board) == len(data.players) and board[:len(ranked)] == ranked
    assert [p.adp for p in ranked] == sorted(p.adp for p in ranked)
    week_one = [data.projection(p.player_id, 1) for p in unranked]
    assert unranked and week_one == sorted(week_one, reverse=True)


def test_available_drops_drafted_players():
    settings, data = fakes.fake_settings(), fakes.fake_season()
    state = rd.new_game(settings, 0)
    first = rd.available(state, data)[0]

    after = rd.make_pick(state, settings, data, first.player_id)

    assert first not in rd.available(after, data) and len(rd.available(after, data)) == len(data.players) - 1
    assert rd.available(drafted(), data)[0].player_id not in rs.rostered(drafted())


# ---------------- making a pick ----------------

def test_a_pick_joins_the_roster_and_is_logged_without_touching_the_old_state():
    settings, data = fakes.fake_settings(), fakes.fake_season()
    state = rd.new_game(settings, 0)
    player = rd.available(state, data)[4]

    after = rd.make_pick(state, settings, data, player.player_id)

    assert rs.user_team(after).roster == (player.player_id,)
    assert after.picks == (rs.Pick(1, 1, 1, player.player_id),)
    event = after.events[-1]
    assert (event.kind, event.team_id, event.week, event.player_ids) == ("draft", 1, 0, (player.player_id,))
    assert player.name in event.text and rd.USER_TEAM_NAME in event.text
    assert state.picks == () and rs.user_team(state).roster == () and state.events == ()
    assert rd.on_the_clock(after, settings) == 2


def test_make_pick_rejects_wrong_turns_unknown_and_taken_players():
    settings, data = fakes.fake_settings(), fakes.fake_season()
    state = rd.new_game(settings, 0)
    top = rd.available(state, data)[0].player_id
    cpu_turn = rd.make_pick(state, settings, data, top)

    with pytest.raises(ValueError, match="not your turn"):
        rd.make_pick(cpu_turn, settings, data, rd.available(cpu_turn, data)[0].player_id)
    with pytest.raises(ValueError, match="is not on the clock"):
        rd.make_pick(cpu_turn, settings, data, rd.available(cpu_turn, data)[0].player_id, team_id=5)
    with pytest.raises(ValueError, match="already been drafted"):
        rd.make_pick(cpu_turn, settings, data, top, team_id=2)
    with pytest.raises(ValueError, match="no player 999"):
        rd.make_pick(state, settings, data, 999)
    with pytest.raises(ValueError, match="draft is over"):
        rd.make_pick(drafted(), settings, data, rd.available(drafted(), data)[0].player_id)
    with pytest.raises(ValueError, match="draft is over"):
        rd.cpu_pick(drafted(), settings, data, 0)


def test_a_user_hoarding_quarterbacks_is_stopped_exactly_when_a_legal_roster_becomes_impossible():
    settings, data, seed = fakes.fake_settings(draft_slot=6), fakes.fake_season(), 4
    state, taken, refused = rd.run_cpu_picks(rd.new_game(settings, seed), settings, data, seed), [], None

    while refused is None:
        quarterback = next(p for p in rd.available(state, data) if p.position == "QB")
        left_after = settings.rounds - len(taken) - 1
        fits = rv.lineup_holes([*taken, "QB"], settings) <= left_after
        try:
            state = rd.run_cpu_picks(rd.make_pick(state, settings, data, quarterback.player_id), settings, data, seed)
            taken.append("QB")
            assert fits
        except ValueError as error:
            refused = str(error)
            assert not fits

    assert len(taken) == 7                                      # 15 picks, 8 other starting slots
    assert "RB, RB, WR, WR, TE, K, DST, FLEX" in refused and "7 picks left" in refused
    final = play_out(state, settings, data, seed)
    mine = positions(final, settings.draft_slot)
    assert Counter(mine)["QB"] == 7 and len(mine) == settings.rounds and rv.can_field_lineup(mine, settings)


def test_autopick_is_the_cpu_logic_without_noise_and_only_on_the_users_turn():
    settings, data = fakes.fake_settings(draft_slot=2), fakes.fake_season()
    waiting = rd.new_game(settings, 0)
    state = rd.run_cpu_picks(waiting, settings, data, 0)

    picked, other_seed = rd.autopick_user(state, settings, data, 0), rd.autopick_user(state, settings, data, 77)

    assert rs.user_team(picked).roster == rs.user_team(other_seed).roster != ()
    assert rd.on_the_clock(picked, settings) == 3
    with pytest.raises(ValueError, match="not your turn"):
        rd.autopick_user(waiting, settings, data, 0)


def test_run_cpu_picks_stops_at_the_user_and_does_nothing_on_the_users_turn():
    settings, data = fakes.fake_settings(draft_slot=5), fakes.fake_season()

    state = rd.run_cpu_picks(rd.new_game(settings, 2), settings, data, 2)

    assert len(state.picks) == 4 and rd.on_the_clock(state, settings) == 5
    assert rd.run_cpu_picks(state, settings, data, 2) is state


# ---------------- finishing ----------------

@pytest.mark.parametrize("teams", SIZES)
def test_the_last_pick_finishes_the_draft_with_a_schedule_and_waiver_order(teams):
    settings, state = fakes.fake_settings(teams=teams, draft_slot=3), drafted(teams)
    ids = set(range(1, teams + 1))

    assert (state.phase, state.week) == ("open", 1)
    assert len(state.picks) == teams * settings.rounds == len(state.events)
    assert state.waiver_order == tuple(range(teams, 0, -1))
    assert len(state.schedule) == settings.regular_season_weeks
    assert all({team for game in week for team in game} == ids for week in state.schedule)
    assert state.schedule == rs.round_robin(state.draft_order, settings.regular_season_weeks, 0)
    assert rs.state_from_json(rs.to_json(state)) == state


def test_a_user_making_the_last_pick_leaves_the_finish_to_run_cpu_picks():
    settings, data, seed = fakes.fake_settings(teams=8, draft_slot=8), fakes.fake_season(), 1
    state = rd.new_game(settings, seed)
    while len(state.picks) < 8 * settings.rounds - 1:
        state = rd.run_cpu_picks(state, settings, data, seed)
        if len(state.picks) < 8 * settings.rounds - 1:
            state = rd.autopick_user(state, settings, data, seed)
    last = rd.cpu_pick(state, settings, data, seed)

    picked = rd.make_pick(state, settings, data, last)
    finished = rd.run_cpu_picks(picked, settings, data, seed)

    assert (picked.phase, rd.on_the_clock(picked, settings)) == ("draft", None)
    assert (finished.phase, finished.week, len(finished.schedule)) == ("open", 1, settings.regular_season_weeks)
    assert rd.autopick_user(state, settings, data, seed).phase == "open"
    with pytest.raises(ValueError, match="only be finished once"):
        rd.finish_draft(finished, settings, data, seed)
    with pytest.raises(ValueError, match="only be finished once"):
        rd.finish_draft(state, settings, data, seed)


# ---------------- simulated drafts ----------------

@pytest.mark.parametrize("teams", SIZES)
def test_every_roster_is_full_legal_within_caps_and_nobody_is_drafted_twice(teams):
    settings = fakes.fake_settings(teams=teams)

    for seed in SEEDS:
        state = drafted(teams, seed)
        assert len(rs.rostered(state)) == teams * settings.rounds == len({p.player_id for p in state.picks})
        for team in state.teams:
            mine = positions(state, team.id)
            assert len(mine) == settings.rounds and rv.can_field_lineup(mine, settings)
            assert all(count <= DEFAULT_CAPS[position] for position, count in Counter(mine).items())
            assert Counter(mine)["RB"] >= 4 and Counter(mine)["WR"] >= 4 and Counter(mine)["K"] == 1


@pytest.mark.parametrize("teams", SIZES)
def test_kickers_defenses_and_backups_wait_for_their_rounds(teams):
    settings = fakes.fake_settings(teams=teams)

    for seed in SEEDS:
        seen: Counter = Counter()
        for pick, player in picks_with_players(drafted(teams, seed)):
            seen[(pick.team_id, player.position)] += 1
            if player.position in ("K", "DST"):
                assert pick.round > settings.rounds - 2
            if player.position in ("QB", "TE") and seen[(pick.team_id, player.position)] == 2:
                assert pick.round >= 10


@pytest.mark.parametrize("teams", SIZES)
def test_the_position_mix_by_round_looks_like_a_real_draft(teams):
    early: Counter = Counter()
    first_qb: list[int] = []

    for seed in SEEDS:
        picks = picks_with_players(drafted(teams, seed))
        early.update(player.position for pick, player in picks if pick.round <= 3)
        for team_id in range(1, teams + 1):
            first_qb.append(next(pick.round for pick, player in picks
                                 if pick.team_id == team_id and player.position == "QB"))

    assert (early["RB"] + early["WR"]) / sum(early.values()) >= 0.75
    assert early["K"] == early["DST"] == 0
    assert sum(4 <= round_ <= 10 for round_ in first_qb) / len(first_qb) >= 0.75
    assert max(first_qb) <= 10


@pytest.mark.parametrize("teams", SIZES)
def test_drafts_follow_adp_loosely_never_exactly(teams):
    data = fakes.fake_season()
    by_adp = [p.player_id for p in rd.available(rd.new_game(fakes.fake_settings(teams=teams), 0), data)]

    for seed in SEEDS:
        state = drafted(teams, seed)
        order = [pick.player_id for pick in state.picks]
        assert order[:ADP_WINDOW] != by_adp[:ADP_WINDOW]
        assert mean_adp_gap(state) <= MAX_MEAN_ADP_GAP
        assert all(player.adp <= pick.overall + rd.REACH_LIMIT
                   for pick, player in picks_with_players(state)[:ADP_WINDOW])
        mixes = {tuple(sorted(Counter(positions(state, team.id)).items())) for team in state.teams}
        assert len(mixes) > 1                                   # CPUs do not all build the same roster


def test_the_same_seed_gives_the_same_draft_and_other_seeds_differ():
    settings, data = fakes.fake_settings(draft_slot=3), fakes.fake_season()

    again = play_out(rd.new_game(settings, 7), settings, data, 7)

    assert again == drafted(12, 7)
    assert len({tuple(p.player_id for p in drafted(12, seed).picks) for seed in SEEDS}) == len(SEEDS)


def test_hard_difficulty_keeps_cpus_closer_to_adp():
    normal = sum(mean_adp_gap(drafted(12, seed)) for seed in SEEDS)
    hard = sum(mean_adp_gap(drafted(12, seed, "hard")) for seed in SEEDS)

    assert hard < normal * 0.8


def test_players_without_adp_go_only_when_nobody_with_adp_fits():
    data = fakes.fake_season()

    assert all(player.adp is not None for seed in SEEDS for _, player in picks_with_players(drafted(12, seed)))
    for seed in SEEDS:                  # 14 teams, 12 kickers and 12 defenses with an ADP
        state = drafted(14, seed)
        for pick, player in picks_with_players(state):
            if player.adp is None:
                earlier = {p.player_id for p in state.picks[:pick.overall - 1]}
                same = [p for p in data.players.values() if p.position == player.position and p.adp is not None]
                assert all(p.player_id in earlier for p in same)


@pytest.mark.parametrize("overrides", [
    {"bench": 0}, {"bench": 12}, {"starters": {"QB": 2, "RB": 2, "WR": 3, "TE": 1, "FLEX": 2, "K": 1, "DST": 1}},
    {"starters": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1}, "bench": 5},
])
def test_other_roster_shapes_still_draft_legal_rosters(overrides):
    settings, data = fakes.fake_settings(teams=10, draft_slot=4, **overrides), fakes.fake_season()

    state = play_out(rd.new_game(settings, 11), settings, data, 11)

    assert state.phase == "open"
    for team in state.teams:
        mine = positions(state, team.id)
        assert len(mine) == settings.rounds and rv.can_field_lineup(mine, settings)
        assert all(position in settings.starters for position in mine)      # no K or DST without a slot


# ---------------- no hindsight ----------------

def test_the_draft_is_identical_when_everything_after_the_preseason_is_different():
    settings, data = fakes.fake_settings(teams=14, draft_slot=3), fakes.fake_season()
    other = fakes.future_changed(data, after_week=1)            # week-1 projections order the no-ADP players
    start = rd.new_game(settings, 6)

    real, altered = drafted(14, 6), play_out(start, settings, other, 6)

    assert dict(other.actual) != dict(data.actual) and dict(other.projected) != dict(data.projected)
    assert altered == real
    assert rd.available(start, other) == rd.available(start, data)
    assert rd.available(real, other) == rd.available(real, data)


def test_players_with_adp_are_drafted_on_adp_alone_and_from_the_week_zero_copy():
    settings, data = fakes.fake_settings(draft_slot=3), fakes.fake_season()
    no_week_one = fakes.future_changed(data, after_week=0)      # even week-1 projections changed
    start = rd.new_game(settings, 8)

    assert play_out(start, settings, no_week_one, 8).picks == drafted(12, 8).picks
    assert play_out(start, settings, data.as_of(0), 8) == drafted(12, 8)
    assert dict(data.as_of(0).actual) == {}                     # nothing to peek at
