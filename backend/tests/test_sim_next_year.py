"""sim_next_year: the offseason turns one season's belief into the next one's (hand-built worlds, no database)."""
from types import MappingProxyType

import numpy as np
import pytest

from services import sim_next_year as ny, sim_talent
from services.replay_pool import PoolPlayer
from services.schedule_strength import Game, Schedule
from services.sim_offseason import Rookie
from services.sim_world import Player, Team, World, params

TEAMS = ("DAL", "KC", "MIA", "PIT")
ROLES = {"QB1": ("QB", 0, 0.15, 1.0), "QB2": ("QB", 0, 0.02, 0.05), "RB1": ("RB", 0.12, 0.6, 0),
         "RB2": ("RB", 0.05, 0.3, 0), "WR1": ("WR", 0.25, 0.01, 0), "WR2": ("WR", 0.18, 0.01, 0),
         "WR3": ("WR", 0.1, 0, 0), "TE1": ("TE", 0.15, 0, 0), "K1": ("K", 0, 0, 1.0), "DST": ("DST", 0, 0, 1.0)}
SLOTS = tuple(ROLES)


def pid(team: str, slot: str) -> int:
    return 100 * (TEAMS.index(team) + 1) + SLOTS.index(slot)


def _player(team: str, slot: str, quality: float = 1.0, **overrides) -> Player:
    position, targets, carries, starts = ROLES[slot]
    norm = params()["positions"].get(position, {})
    fields = dict(targets=targets, carries=carries, starts=starts, catch=norm.get("catch", 0.0),
                  ypc=norm.get("ypc", 0.0), rec_td=norm.get("rec_td", 0.0), ypr=norm.get("ypr", 0.0),
                  rush_td=norm.get("rush_td", 0.0), quality=quality, int_rate=0.02 if position == "QB" else 0.0,
                  games=17.0, out_weeks=2)
    return Player(pid(team, slot), position, team, **{**fields, **overrides})


def _world(extra: tuple[Player, ...] = ()) -> World:
    league = params()["league"]
    players = {p.player_id: p for p in (*(_player(team, slot, 1.05 if slot == "QB1" else 0.9)
                                          for team in TEAMS for slot in SLOTS), *extra)}
    teams = {team: Team(team, league["targets"] * 1.2, league["carries"], 0.0, 0.0, 1.1, 0.9, 1.0, 1.05)
             for team in TEAMS}
    return World(MappingProxyType(players), MappingProxyType(teams))


def _names(world: World) -> dict[int, PoolPlayer]:
    return {i: PoolPlayer(i, f"Player {i}", p.position) for i, p in world.players.items()}


ROOKIES = (Rookie("Top Receiver", "WR", "MIA", 1, "Ohio State"), Rookie("Late Back", "RB", "PIT", 150, "Iowa"),
           Rookie("Early Receiver", "WR", "DAL", 4, "Texas"), Rookie("Day Two Back", "RB", "KC", 40, "Utah"))


def _next(world: World | None = None, moves=None, rookies=ROOKIES, starts=None):
    world = world or _world()
    return ny.next_year(world, _names(world), dict.fromkeys(world.players, 3), ny.rookie_ids(2027, rookies),
                        moves or {}, starts)


def test_retired_players_leave_moved_ones_change_teams_and_rookies_arrive():
    retired, moved = pid("MIA", "WR1"), pid("KC", "RB1")
    belief, pool = _next(moves={retired: None, moved: "PIT"})

    assert retired not in belief.players and retired not in pool
    assert belief.players[moved].team == "PIT" and pool[moved].adp_team == "PIT"
    ids = ny.rookie_ids(2027, ROOKIES)
    assert len(ids) == len(ROOKIES) and all(i > ny.ROOKIE_ID_BASE for i in ids)
    assert {pool[i].name for i in ids} == {r.name for r in ROOKIES}
    assert all(belief.players[i].games == 0 and belief.players[i].team == r.team for i, r in ids.items())
    assert all(p.out_weeks == 0 for p in belief.players.values())          # everyone healthy in August
    assert all(pool[i].adp_team == p.team for i, p in belief.players.items())


def test_the_offseason_is_deterministic():
    assert _next() == _next()


def test_a_team_nobody_joined_or_left_keeps_its_total_work():
    world = _world()
    belief, _ = _next(world, rookies=())
    for team in TEAMS:
        for field in ("targets", "carries"):
            before = sum(getattr(p, field) for p in world.players.values() if p.team == team)
            after = sum(getattr(p, field) for p in belief.players.values() if p.team == team)
            assert after == pytest.approx(before)


def test_rookie_roles_fall_with_the_pick_and_are_not_extrapolated_above_the_floor():
    team = _world().teams["MIA"]
    role = {pick: ny.rookie_player(1, Rookie("R", "RB", "MIA", pick, "X"), team).carries for pick in (1, 4, 30, 200)}
    assert role[1] == role[4] > role[30] > role[200] > 0
    receiver = ny.rookie_player(2, Rookie("W", "WR", "MIA", 10, "X"), team)
    assert receiver.ypc == pytest.approx(params()["positions"]["WR"]["ypc"] * team.passer)


def test_a_rookie_qb_starts_only_where_the_job_is_open_or_he_went_very_high():
    rookie = Rookie("First Round Passer", "QB", "PIT", 10, "Penn State")
    rid = ny.ROOKIE_ID_BASE + 2027 * ny.ROOKIE_ID_SEASON + 10
    assert ny.ROOKIE_QB_NAMED <= ny._line("QB", "week1", 10) < ny.ROOKIE_QB_FRANCHISE
    behind, _ = _next(rookies=(rookie,))                                   # above-average incumbent keeps it
    assert behind.players[pid("PIT", "QB1")].starts > behind.players[rid].starts >= sim_talent.ROOKIE_HOPEFUL
    open_job, _ = _next(moves={pid("PIT", "QB1"): None}, rookies=(rookie,))
    assert open_job.players[rid].starts == 1.0
    weak = _world((_player("PIT", "QB1", 0.92),))                          # a below-average incumbent
    named, _ = _next(weak, rookies=(rookie,))
    assert named.players[rid].starts == 1.0 > named.players[pid("PIT", "QB1")].starts
    first_pick = Rookie("Franchise Passer", "QB", "PIT", 1, "Ohio State")
    franchise, _ = _next(rookies=(first_pick,))                            # the top picks start anyway
    assert franchise.players[ny.ROOKIE_ID_BASE + 2027 * ny.ROOKIE_ID_SEASON + 1].starts == 1.0


def test_the_healthy_incumbent_gets_his_job_back():
    hurt = _world((_player("KC", "QB1", 1.05, starts=0.4), _player("KC", "QB2", 0.9, starts=0.6)))
    belief, _ = _next(hurt, rookies=(), starts={pid("KC", "QB1"): [1, 2, 3], pid("KC", "QB2"): [4, 5, 6, 7]})
    assert belief.players[pid("KC", "QB1")].starts == 1.0
    assert belief.players[pid("KC", "QB2")].starts == ny.VETERAN_BACKUP_STARTS


def test_a_team_that_loses_its_only_kicker_signs_a_spare():
    spare = Player(999, "K", "KC", starts=0.3)
    belief, _ = _next(_world((spare,)), moves={pid("MIA", "K1"): None}, rookies=())
    assert belief.players[999].team == "MIA"
    assert all(any(p.team == team and p.position == "K" for p in belief.players.values()) for team in TEAMS)


def _schedule() -> Schedule:
    pairs = (("DAL", "KC"), ("MIA", "PIT"))
    games = tuple(Game(week, *(pair if week % 2 else pair[::-1]), None, None, None, None, gameday="2027-09-12")
                  for week in range(1, 18) for pair in pairs)
    opponents = {}
    for g in games:
        opponents[(g.home_team, g.week)], opponents[(g.away_team, g.week)] = g.away_team, g.home_team
    return Schedule(games, MappingProxyType(opponents))


def test_the_season_base_holds_every_player_who_dresses_before_anything_happened():
    belief, pool = _next()
    base = ny.season_base(2027, belief, pool, _schedule(), "half", None, {})

    assert not base.actual and not base.team_by_week and not base.injuries and base.last_week == 17
    known = base.as_of(1).players
    depth = [i for i, p in base.players.items() if p.adp is None and (i, 1) in base.projected]
    assert depth and set(depth) <= set(known)                      # depth players exist from week 1
    for position in ("K", "DST", "QB"):
        assert {belief.players[i].team for i in known if pool[i].position == position} == set(TEAMS)
    assert all(base.team_at(i, 1) == belief.players[i].team for i in known)


def test_rookies_are_the_least_certain_and_prospects_take_over_at_the_rookie_rate():
    knobs = sim_talent.settings()
    assert sim_talent._spread(knobs["role_sd"], 0, knobs) > sim_talent._spread(knobs["role_sd"], 4.25, knobs)
    starter, prospect, veteran = (Player(1, "QB", "MIA", starts=1.0, games=17), Player(2, "QB", "MIA", starts=0.4),
                                  Player(3, "QB", "MIA", starts=0.05, games=17))

    def benched(backup: Player, whole: bool) -> float:
        dice = np.random.default_rng(5)
        return np.mean([any(c.kind == "bench" for c in sim_talent._changes([starter, backup], dice, (2, 15), knobs, whole))
                        for _ in range(2000)])

    assert benched(prospect, True) == pytest.approx(knobs["rookie_bench_rate"], abs=0.04)
    assert benched(veteran, True) == pytest.approx(knobs["bench_rate"], abs=0.03)
    assert benched(prospect, False) == pytest.approx(knobs["bench_rate"], abs=0.03)   # mid-season: the usual rate


def test_every_player_of_the_new_season_stays_in_the_public_view_until_he_plays():
    """A backup QB or a WR6 has no ADP and the market projects him nothing, yet he must be there to add (and in
    the belief, to step in) from week 1, not only after his first game."""
    from services import sim_season, sim_world

    belief, pool = _next()
    base = ny.season_base(2027, belief, pool, _schedule(), "half", None, {})
    season = sim_season.simulate(base, 4, 1, belief)
    depth = [i for i, p in base.players.items() if p.adp is None]
    assert any(belief.players[i].position == "QB" for i in depth)
    for week in range(1, season.last_week + 1):
        known = season.as_of(week).players
        assert set(belief.players) <= set(known), week
        assert set(belief.players) <= set(sim_world.estimate(season, week, belief).players), week
