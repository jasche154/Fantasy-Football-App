"""sim_next_year: the offseason turns one season's belief into the next one's (hand-built worlds, no database)."""
from dataclasses import replace
from types import MappingProxyType, SimpleNamespace

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
    belief, _ = _next(hurt, rookies=(), starts={pid("KC", "QB1"): [(1, "KC"), (2, "KC"), (3, "KC")],
                                                pid("KC", "QB2"): [(4, "KC"), (5, "KC"), (6, "KC"), (7, "KC")]})
    assert belief.players[pid("KC", "QB1")].starts == 1.0
    assert belief.players[pid("KC", "QB2")].starts == ny.VETERAN_BACKUP_STARTS


def test_a_fill_in_elsewhere_does_not_take_the_job_from_the_teams_own_starter():
    """Starts for another team count only when he was that team's main starter (2024: Flacco's five fill-in
    starts at CLE did not make him IND's starter over Richardson; a signed starter still is)."""
    fill_in, own = pid("DAL", "QB2"), pid("PIT", "QB1")
    starts = {fill_in: [(w, "DAL") for w in range(1, 6)], pid("DAL", "QB1"): [(w, "DAL") for w in range(6, 18)],
              own: [(w, "PIT") for w in range(1, 5)]}
    belief, _ = _next(rookies=(), moves={fill_in: "PIT"}, starts=starts)
    assert belief.players[own].starts == 1.0 > belief.players[fill_in].starts
    signed, _ = _next(rookies=(), moves={pid("DAL", "QB1"): "PIT"}, starts=starts)
    assert signed.players[pid("DAL", "QB1")].starts == 1.0 > signed.players[own].starts


def test_a_qb_traded_in_mid_season_brings_no_claim_from_his_old_team():
    """2026: Brissett led ARI for 8 weeks, then started once for BAL; Lamar (4 starts, hurt late, low starts
    weight) is still BAL's starter in August."""
    traded, own = pid("MIA", "QB1"), pid("KC", "QB1")
    world = _world((replace(_player("MIA", "QB1", 1.0), team="KC", starts=0.6), _player("KC", "QB1", 1.05, starts=0.38)))
    starts = {traded: [*((w, "MIA") for w in range(1, 9)), (9, "KC")], own: [(w, "KC") for w in range(1, 5)]}
    belief, _ = _next(world, rookies=(), starts=starts)
    assert belief.players[own].starts == 1.0 > belief.players[traded].starts


def test_a_starter_traded_at_the_deadline_keeps_his_claim_over_a_fill_in():
    """He led MIA for 13 weeks, then started twice for KC, whose own week-1 starter is gone: KC's 10-start
    fill-in is not its August starter."""
    traded, fill_in, opener = pid("MIA", "QB1"), pid("KC", "QB1"), pid("KC", "QB2")
    world = _world((replace(_player("MIA", "QB1", 1.0), team="KC", starts=0.3),
                    _player("KC", "QB1", 0.9, starts=0.6), _player("KC", "QB2", 0.85, starts=0.1)))
    starts = {traded: [*((w, "MIA") for w in range(1, 14)), (16, "KC"), (17, "KC")],
              fill_in: [(w, "KC") for w in range(6, 16)], opener: [(w, "KC") for w in range(1, 6)]}
    belief, _ = _next(world, rookies=(), moves={opener: "MIA"}, starts=starts)
    assert belief.players[traded].starts == 1.0 > belief.players[fill_in].starts


def test_each_start_counts_for_the_team_he_played_that_game_for(monkeypatch):
    """A QB traded before week 9 started week 9 for his new team (team_at would say the old one)."""
    from services import replay_pool, sim_metrics

    data = SimpleNamespace(team_by_week={7: ((7, "ARI"), (8, "ARI"), (9, "BAL"))}, players={})
    data.team_at = lambda player_id, week: replay_pool.SeasonData.team_at(data, player_id, week)
    monkeypatch.setattr(sim_metrics, "qb_starts", lambda _: {7: [7, 8, 9]})
    assert ny.starts_by_team(data) == {7: [(7, "ARI"), (8, "ARI"), (9, "BAL")]}


def test_receivers_carry_their_august_starter_and_a_qbs_rushing_keeps_its_own_regression():
    """A receiver's yards and TDs per catch are held at an average passer and scaled to the new starter's
    regressed quality (his QB's part regresses once, with the QB); a QB's rushing uses the QB keep."""
    world = _world()
    belief, _ = _next(world, rookies=())
    for team in TEAMS:
        starter = belief.players[pid(team, "QB1")]
        assert belief.teams[team].passer == pytest.approx(starter.quality)
    left, _ = _next(world, moves={pid("MIA", "QB1"): None}, rookies=())
    wr, before = left.players[pid("MIA", "WR1")], belief.players[pid("MIA", "WR1")]
    assert wr.ypc / before.ypc == pytest.approx(left.teams["MIA"].passer / belief.teams["MIA"].passer)
    assert ny.keep_of("QB", "ypr", scaled=False) == ny.stored()["efficiency_keep"]["QB ypr"]
    assert ny.keep_of("RB", "ypr", scaled=False) == ny.stored()["efficiency_keep"]["ypr"]
    scale = ny.stored()["efficiency_scale"]["RB"]
    assert ny.keep_of("RB", "ypr") == pytest.approx(1 - scale * (1 - ny.keep_of("RB", "ypr", scaled=False)))


def test_a_hyped_rookie_costs_more_and_is_trusted_with_more_work():
    """hype multiplies the market's premium in full and his role by hype ^ HYPE_ROLE (the coaches buy part of it)."""
    plain, hyped = Rookie("W", "WR", "MIA", 1, "X"), Rookie("W", "WR", "MIA", 1, "X", hype=1.5)
    team = _world().teams["MIA"]
    assert ny.rookie_premium({1: hyped})[1] == pytest.approx(1.5 * ny.rookie_premium({1: plain})[1])
    a, b = ny.rookie_player(1, plain, team), ny.rookie_player(1, hyped, team)
    assert b.targets == pytest.approx(a.targets * 1.5 ** ny.HYPE_ROLE) and b.ypc == a.ypc


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

    def benched(backup: Player, whole: bool, rank: int = 20, share: float = 1.0) -> float:
        dice = np.random.default_rng(5)
        return np.mean([any(c.kind == "bench" for c in sim_talent._changes([starter, backup], dice, (2, 15), knobs, whole,
                                                                          {1: rank}, share))
                        for _ in range(2000)])

    usual = sim_talent.bench_chance(20)
    assert benched(prospect, True) == pytest.approx(knobs["rookie_bench_rate"], abs=0.04)
    assert benched(veteran, True) == pytest.approx(usual, abs=0.03)
    assert benched(prospect, False) == pytest.approx(usual, abs=0.03)       # mid-season: the usual rate
    assert benched(veteran, True, rank=1) < benched(veteran, True, rank=32)   # a top-rated starter keeps his job
    assert benched(veteran, False, share=0.4) == pytest.approx(0.4 * usual, abs=0.03)    # fewer weeks left


def test_the_market_pays_a_rookie_premium_on_his_price_only():
    """Real drafters pay for a rookie's upside: his ADP rises with the premium, every position's veterans keep
    their order, and the projections (the belief's) do not move."""
    belief, pool = _next()
    ids = ny.rookie_ids(2027, ROOKIES)
    premium = ny.rookie_premium(ids)
    assert set(premium) == set(ids) and all(m >= 1.0 for m in premium.values())
    assert ny.premium_at({"WR": [0.5, -0.5]}, "WR", 1) > 1.0 == ny.premium_at({}, "QB", 1)
    plain = ny.season_base(2027, belief, pool, _schedule(), "half", None, {})
    paid = ny.season_base(2027, belief, pool, _schedule(), "half", None, {}, premium={pid: 1.5 for pid in ids})
    rookie = next(pid for pid, r in ids.items() if r.name == "Early Receiver")
    assert paid.players[rookie].adp < plain.players[rookie].adp
    for position in ("QB", "RB", "WR", "TE"):              # (a rookie moving up can move replacement levels)
        veterans = [pid for pid, p in plain.players.items() if p.adp and pid not in ids and p.position == position]
        assert sorted(veterans, key=lambda p: plain.players[p].adp) == sorted(veterans, key=lambda p: paid.players[p].adp)
    assert paid.projected == plain.projected                  # the price only: the projections are the belief's


def test_the_rookie_premium_moves_the_price_and_nothing_the_season_does():
    """Same seed, with and without a large premium: the results, projections, tags, buzz and the in-season expert
    ranks are identical; only the ADP and the preseason (week-0) expert ranks move."""
    from services import sim_season

    belief, pool = _next()
    ids = ny.rookie_ids(2027, ROOKIES)
    plain = ny.season_base(2027, belief, pool, _schedule(), "half", None, {})
    paid = ny.season_base(2027, belief, pool, _schedule(), "half", None, {}, premium=dict.fromkeys(ids, 3.0))
    for seed in (1, 2):
        a, b = sim_season.simulate(plain, seed, 1, belief), sim_season.simulate(paid, seed, 1, belief)
        assert dict(a.actual) == dict(b.actual) and dict(a.box) == dict(b.box)
        assert dict(a.projected) == dict(b.projected)
        assert dict(a.injuries) == dict(b.injuries) and dict(a.notes) == dict(b.notes)
        in_season = [{k: v for k, v in s.expert_ranks.items() if k[1] > 0} for s in (a, b)]
        assert in_season[0] == in_season[1]
        assert {i: p.adp for i, p in a.players.items()} != {i: p.adp for i, p in b.players.items()}
        assert ({k: v for k, v in a.expert_ranks.items() if k[1] == 0}
                != {k: v for k, v in b.expert_ranks.items() if k[1] == 0})


def test_the_fit_writes_its_file_once_at_the_end_and_never_after_a_failed_step(tmp_path, monkeypatch):
    """The premium step needs the network and runs last on the model just fitted (read from memory): an
    interrupted run leaves the checked-in file as it was."""
    import json
    from contextlib import nullcontext

    import database.db
    from services import sim_next_year_fit as fit

    path, model, seen, stored = tmp_path / "sim_next_year.json", {"age": {}, "keep": {}}, [], ny.stored
    path.write_text("checked in\n")
    monkeypatch.setattr(fit, "PATH", path)
    monkeypatch.setattr(database.db, "SessionLocal", lambda: nullcontext(None))
    monkeypatch.setattr(fit, "observations", lambda session, seasons: {})
    monkeypatch.setattr(fit, "summarize", lambda rows, seasons: dict(model))
    monkeypatch.setattr(fit, "whole_season_fits", lambda session, seasons, fitted: {})

    def offline(session, seasons):
        seen.append(ny.stored())
        raise ConnectionError

    monkeypatch.setattr(fit, "premium_cases", offline)
    with pytest.raises(ConnectionError):
        fit.main(["--fit"])
    assert path.read_text() == "checked in\n" and ny.stored is stored
    assert seen == [{**model, "rookie_premium": {}}]                 # measured with no premium yet

    monkeypatch.setattr(fit, "premium_cases", lambda session, seasons: [])
    monkeypatch.setattr(fit, "_premium_fit", lambda cases: {"RB": [0.2, 0.0]})
    fit.main(["--fit"])
    assert json.loads(path.read_text()) == {**model, "rookie_premium": {"RB": [0.2, 0.0]}}


REAL_EXPERT_ADP_R = 0.98  # real week-0 expert vs ADP position ranks, real top-150 ADP, 2022-2025 (0.981-0.987)


def test_the_preseason_experts_rank_players_the_way_the_preseason_market_drafts_them():
    """The draft room orders by the week-0 expert ranks and shows the ADP next to them; in a real August the two
    agree closely, so they must here too, even when last season's record (which the market leans on) disagrees
    with the belief."""
    from services import sim_metrics, sim_season

    spread = dict(zip(TEAMS, (1.4, 1.15, 0.9, 0.7)))             # teams unlike each other, as real ones are
    world = _world()
    belief, pool = _next(replace(world, players=MappingProxyType({i: replace(p, targets=p.targets * spread[p.team],
                                                                             carries=p.carries * spread[p.team])
                                                                  for i, p in world.players.items()})))
    flipped = {pid(team, slot): points for team in ("DAL", "KC")
               for slot, points in (("WR1", 2.0), ("WR3", 22.0), ("RB1", 3.0), ("RB2", 20.0))}
    base = ny.season_base(2027, belief, pool, _schedule(), "half", None, {}, record=flipped)
    adp, agreement = sim_metrics.adp_ranks(base), []
    for seed in (1, 2, 3):
        season = sim_season.simulate(base, seed, 1, belief)
        experts = {p: rank for (p, week), rank in season.expert_ranks.items() if week == 0}
        skill = sorted(p for p in set(experts) & set(adp) if pool[p].position in sim_metrics.SKILL)
        agreement.append(sim_metrics.spearman([experts[p] for p in skill], [adp[p] for p in skill]))
        assert all(adp[p] <= 2 * experts[p] + 1 for p in skill if experts[p] <= 12)
        assert all(experts[p] < experts[q] for p in adp for q in experts            # the undrafted rank last
                   if q not in adp and pool[p].position == pool[q].position)
    assert np.mean(agreement) >= REAL_EXPERT_ADP_R - 0.02


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
