"""sim_rookie_draft: the seeded draft of a future season's rookie class (hand-built worlds, no database)."""
from dataclasses import replace
from types import MappingProxyType

from services import sim_rookie_draft as rd
from services.schedule_strength import Game
from services.sim_offseason import Rookie
from services.sim_schedule import TEAMS
from services.sim_world import Player, Team, World

CLASS = (Rookie("Star Receiver", "WR", "MIA", 1, "Ohio State"), Rookie("Top Passer", "QB", "NYJ", 3, "Texas"),
         Rookie("Second Passer", "QB", "ARI", 12, "Oregon"), Rookie("Big Back", "RB", "NYG", 20, "Florida"),
         Rookie("Day Three End", "TE", "KC", 120, "Iowa"))


def _world(established: set[str]) -> World:
    players = {}
    for i, team in enumerate(TEAMS):
        good = team in established
        players[i * 10 + 1] = Player(i * 10 + 1, "QB", team, starts=1.0, quality=1.05 if good else 0.85, games=17.0)
        players[i * 10 + 2] = Player(i * 10 + 2, "RB", team, targets=0.05, carries=0.3 + 0.01 * i, games=17.0)
        players[i * 10 + 3] = Player(i * 10 + 3, "WR", team, targets=0.25, games=17.0)
        players[i * 10 + 4] = Player(i * 10 + 4, "WR", team, targets=0.1 + 0.005 * i, games=17.0)
        players[i * 10 + 5] = Player(i * 10 + 5, "TE", team, targets=0.1 + 0.003 * i, games=17.0)
    teams = {t: Team(t, 34.0, 26.0, 0.1, 0.1) for t in TEAMS}
    return World(MappingProxyType(players), MappingProxyType(teams))


def _games() -> list[Game]:
    """Week 1: each team beats the next one by its index (so TEAMS[-1] loses most); week 2 unplayed."""
    games = [Game(1, TEAMS[i], TEAMS[(i + 1) % 32], 30, 10, None, None) for i in range(0, 32, 2)]
    return games + [Game(2, TEAMS[i], TEAMS[(i + 1) % 32], None, None, None, None) for i in range(1, 32, 2)]


def test_final_wins_keep_real_results_and_simulate_the_rest():
    wins = rd.final_wins(_games(), seed=3)
    assert wins == rd.final_wins(_games(), seed=3)
    assert sum(wins.values()) == 32                     # 16 real games + 16 simulated, a win each
    assert all(wins[TEAMS[i]] >= 1 for i in range(0, 32, 2))      # the real week-1 winners keep their win


def test_the_worst_record_picks_first():
    wins = {team: float(i) for i, team in enumerate(TEAMS)}
    assert rd.draft_order(wins, seed=1)[:3] == list(TEAMS[:3])


def test_the_same_seed_drafts_the_same_class_and_seeds_differ():
    world, order = _world(set()), list(TEAMS)
    need = rd.needs(world)
    first = rd.draft(CLASS, order, need, seed=5)
    assert first == rd.draft(CLASS, order, need, seed=5)
    teams = {tuple(r.team for r in rd.draft(CLASS, rd.draft_order(rd.final_wins(_games(), s), s), need, s))
             for s in range(8)}
    assert len(teams) > 1
    assert sorted(r.name for r in first) == sorted(r.name for r in CLASS)
    assert len({r.pick for r in first}) == len(CLASS)


def test_a_team_with_an_established_qb_does_not_take_one_early():
    established = set(TEAMS[:20])
    order = list(TEAMS)
    for seed in range(10):
        for rookie in rd.draft(CLASS, order, rd.needs(_world(established)), seed):
            if rookie.position == "QB" and rookie.pick <= 32 * rd.QB_NEED_ROUNDS:
                assert rookie.team not in established, (seed, rookie)


def test_a_consensus_top_pick_goes_near_the_top_and_a_lock_keeps_his_team():
    locked = (Rookie("Locked Back", "RB", "LAC", 9, "USC", lock=True), *CLASS)
    for seed in range(10):
        drafted = {r.name: r for r in rd.draft(locked, list(TEAMS), rd.needs(_world(set())), seed)}
        assert drafted["Star Receiver"].pick <= 6
        assert (drafted["Locked Back"].team, drafted["Locked Back"].pick) == ("LAC", 9)
        assert drafted["Day Three End"].pick > 32


def test_needs_follow_the_rooms():
    world = _world({"LAC"})
    need = rd.needs(world)
    assert "QB" not in need["LAC"] and "QB" in need["MIA"]
    weakest = min(TEAMS, key=lambda t: world.players[TEAMS.index(t) * 10 + 2].carries)
    assert "RB" in need[weakest]


def test_a_starter_hurt_late_is_restored_before_the_needs():
    """2026: Lamar's decayed starts weight (0.38) looked like a backup's, so BAL took rookie QBs; with the
    season's starts his August job is restored and BAL needs none."""
    lamar = TEAMS.index("BAL") * 10 + 1
    world = _world({"BAL"})
    world = World(MappingProxyType({**world.players, lamar: replace(world.players[lamar], starts=0.38)}), world.teams)
    assert "QB" in rd.needs(world)["BAL"]
    assert "QB" not in rd.needs(world, {}, {lamar: [(w, "BAL") for w in range(1, 5)]})["BAL"]


def test_a_team_whose_starter_moved_away_needs_a_qb():
    bal = TEAMS.index("BAL") * 10 + 1
    world = _world({"BAL"})
    assert "QB" not in rd.needs(world)["BAL"]
    moved = rd.needs(world, {bal: "MIA"}, {bal: [(w, "BAL") for w in range(1, 18)]})
    assert "QB" in moved["BAL"] and "QB" not in moved["MIA"]


def test_a_team_that_signs_another_teams_starter_needs_no_qb():
    """LAC's starter, hurt late (low starts weight), signs with BAL, whose own QB is weak: BAL is set, LAC is not."""
    signed, own = TEAMS.index("LAC") * 10 + 1, TEAMS.index("BAL") * 10 + 1
    world = _world({"LAC"})
    world = World(MappingProxyType({**world.players, signed: replace(world.players[signed], starts=0.38)}), world.teams)
    starts = {signed: [(w, "LAC") for w in range(1, 11)], own: [(w, "BAL") for w in range(1, 6)]}
    assert "QB" in rd.needs(world, {signed: "BAL"})["BAL"]                # his decayed weight alone: a backup
    need = rd.needs(world, {signed: "BAL"}, starts)
    assert "QB" not in need["BAL"] and "QB" in need["LAC"]


def test_prospects_nobody_takes_go_to_the_first_open_slots_after_the_last_pick(monkeypatch):
    monkeypatch.setattr(rd, "TAKE_WITHOUT_NEED", 0.0)        # a rank-262 prospect no team needs is never taken
    late = (Rookie("Late Back", "RB", "KC", 262, "Iowa"), Rookie("Late End", "TE", "KC", 262, "Utah"))
    locked = Rookie("Locked Passer", "QB", "DAL", 3, "USC", lock=True)
    order = list(TEAMS)
    for seed in range(6):
        drafted = {r.name: r for r in rd.draft((CLASS[0], locked, *late), order, {}, seed)}
        last = drafted["Star Receiver"].pick
        open_slots = [s for s in range(last + 1, last + 4) if s != locked.pick][:2]
        assert sorted((drafted[r.name].pick, drafted[r.name].team) for r in late) == [   # in board order
            (s, order[(s - 1) % 32]) for s in open_slots], seed
        assert (drafted["Locked Passer"].team, drafted["Locked Passer"].pick) == ("DAL", 3)


def test_a_much_stronger_home_team_wins_most_of_its_simulated_games():
    strong, weak = TEAMS[0], TEAMS[1]
    played = [Game(w, strong, weak, 35, 10, None, None) for w in range(1, 5)]     # +25 a game: a rating gap
    left = [Game(w, strong, weak, None, None, None, None) for w in range(5, 9)]
    rematch = [Game(w, weak, strong, None, None, None, None) for w in range(5, 9)]
    home_wins = sum(rd.final_wins(played + left, s)[strong] - 4 for s in range(50)) / 200
    away_wins = sum(rd.final_wins(played + rematch, s)[strong] - 4 for s in range(50)) / 200
    assert home_wins > 0.75 and away_wins > 0.65 and home_wins > away_wins   # rating first, home edge on top


def test_the_home_edge_favors_the_home_side_of_an_even_matchup():
    even = [Game(w, TEAMS[0], TEAMS[1], None, None, None, None) for w in range(1, 1001)]
    wins = rd.final_wins(even, seed=7)
    assert wins[TEAMS[0]] > wins[TEAMS[1]] + 30                  # P(home) = Phi(1.5 / 13.5) = 0.544
