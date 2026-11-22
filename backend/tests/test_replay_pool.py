"""Replay League season data: the fake season itself, and loading a season back from the real tables."""
import time
from collections import Counter

import pytest
from sqlalchemy import create_engine, delete, insert, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import (
    Base, KickerGame, NflGame, Player, PlayerStatsWeekly, ReplayAdp, ReplayInjury, ReplayProjection, TeamGameStats,
)
from services import kdst_model, replay_pool, replay_state, replay_values
from services.injury_recovery import games_to_weeks
from services.replay_pool import ALL_POSITIONS, SKILL, SeasonData
from services.valuation import NoDataError
from tests import replay_fakes as fakes

WEEKS = range(1, 18)


def _ppg(data: SeasonData, player_id: int) -> float:
    played = [data.actual[(player_id, w)] for w in WEEKS if (player_id, w) in data.actual]
    return sum(played) / len(played) if played else 0.0


def _best_ppg(data: SeasonData, position: str, count: int) -> list[float]:
    rates = sorted((_ppg(data, p.player_id) for p in data.players.values() if p.position == position), reverse=True)
    return rates[:count]


def _fresh_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return engine


# ---------------- the fake season ----------------

def test_fake_season_is_fast_and_deterministic():
    fakes._season.cache_clear()
    started = time.perf_counter()
    first = fakes.fake_season(seed=3)
    elapsed = time.perf_counter() - started
    fakes._season.cache_clear()
    second = fakes.fake_season(seed=3)

    assert elapsed < 1.0
    assert first is not second
    assert dict(first.actual) == dict(second.actual) and dict(first.projected) == dict(second.projected)
    assert dict(first.players) == dict(second.players) and first.schedule == second.schedule
    assert fakes.fake_season(seed=3) is second                       # cached per arguments
    assert dict(fakes.fake_season(seed=4).actual) != dict(first.actual)


def test_fake_season_has_a_full_pool_with_late_kickers_and_defenses():
    data = fakes.fake_season()

    counts = Counter(p.position for p in data.players.values())
    drafted = [p for p in data.players.values() if p.adp is not None]
    assert counts == fakes.POOL and set(counts) == set(ALL_POSITIONS)
    assert len(drafted) == 220
    assert Counter(p.position for p in drafted)["K"] == Counter(p.position for p in drafted)["DST"] == 12
    assert min(p.adp for p in drafted if p.position not in SKILL) >= 150
    assert min(p.adp for p in drafted) < 2 and all(p.adp_stdev for p in drafted if p.position in SKILL)
    assert data.last_week == 17 and data.scoring == "half" and data.season == fakes.SEASON


def test_fake_season_scores_like_half_ppr_fantasy():
    data = fakes.fake_season()

    top = {position: sum(_best_ppg(data, position, 3)) / 3 for position in SKILL}
    kickers, defenses = _best_ppg(data, "K", 12), _best_ppg(data, "DST", 12)
    assert 19 <= top["QB"] <= 27 and 17 <= top["RB"] <= 25 and 16 <= top["WR"] <= 23 and 11 <= top["TE"] <= 17
    assert 7 <= sum(kickers) / 12 <= 10 and 6 <= sum(defenses) / 12 <= 10
    assert all(points == int(points) for (pid, _), points in data.actual.items()
               if data.players[pid].position in ("K", "DST"))
    assert min(data.actual.values()) >= -4


def test_fake_season_schedule_has_every_team_playing_except_one_bye_in_weeks_5_to_12():
    data = fakes.fake_season()

    for team in fakes.TEAMS:
        off = [week for week in WEEKS if data.schedule.opponent(team, week) is None]
        assert len(off) == 1 and off[0] in fakes.BYE_WEEKS
    for (team, week), opponent in data.schedule.opponents.items():
        assert data.schedule.opponents[(opponent, week)] == team and opponent != team
    assert Counter(game.week for game in data.schedule.games)[1] == 16
    assert all(game.home_score is None for game in data.schedule.games)      # no final scores to peek at


def test_fake_adp_tracks_talent_without_matching_it():
    data = fakes.fake_season()
    backs = sorted((p for p in data.players.values() if p.position == "RB" and p.adp is not None),
                   key=lambda p: p.adp)

    early = sum(_ppg(data, p.player_id) for p in backs[:12]) / 12
    late = sum(_ppg(data, p.player_id) for p in backs[-12:]) / 12
    by_points = sorted(backs, key=lambda p: -_ppg(data, p.player_id))

    assert early > late + 5
    assert [p.player_id for p in by_points[:12]] != [p.player_id for p in backs[:12]]


def test_fake_season_scripted_stories():
    data = fakes.fake_season()
    breakout, bust, star = (data.players[i] for i in (fakes.BREAKOUT_ID, fakes.BUST_ID, fakes.INJURED_STAR_ID))

    assert breakout.adp > 120 and _ppg(data, breakout.player_id) > 14
    assert data.projection(breakout.player_id, 1) < 8 < data.projection(breakout.player_id, 8)
    assert bust.adp < 15 and _ppg(data, bust.player_id) < 9
    assert data.projection(bust.player_id, 1) > 14 > data.projection(bust.player_id, 9)
    assert star.adp < 12
    for week in fakes.INJURED_WEEKS:
        assert data.projection(star.player_id, week) == 0.0 and (star.player_id, week) not in data.actual
        assert not data.on_bye(star.player_id, week)
    assert data.projection(star.player_id, 5) > 10 and data.projection(star.player_id, 10) > 10
    assert data.on_bye(star.player_id, 12)


def test_a_drafted_player_who_never_plays_is_in_the_pool_on_his_adp_team():
    data = fakes.fake_season()
    ghost = data.players[fakes.NEVER_PLAYED_ID]

    assert ghost.adp < 100 and ghost.position == "WR" and ghost.adp_team in fakes.TEAMS
    assert not any(pid == ghost.player_id for pid, _ in (*data.actual, *data.projected))
    assert fakes.NEVER_PLAYED_ID not in data.team_by_week and data.team_at(ghost.player_id, 9) == ghost.adp_team
    assert [week for week in WEEKS if data.on_bye(ghost.player_id, week)] == [
        week for week in WEEKS if data.schedule.opponent(ghost.adp_team, week) is None]


def test_fake_schedule_has_game_rows_the_lineup_engine_can_count():
    data = fakes.fake_season()
    team = data.team_at(fakes.INJURED_STAR_ID, 6)

    assert len(data.schedule.games) == 16 * 17 - 16                             # four teams rest in each of 8 weeks
    assert {(g.home_team, g.week) for g in data.schedule.games} <= set(data.schedule.opponents)
    assert games_to_weeks(2.0, team, data.schedule, 6) == 2.0
    assert games_to_weeks(6.5, team, data.schedule, 6) == 7.5                   # his week-12 bye is not a game


def test_team_at_follows_a_mid_season_team_change_point_in_time():
    data = fakes.fake_season()
    games = dict(data.team_by_week[fakes.TRADED_ID])
    old, new = games[fakes.TRADED_WEEK - 1], games[fakes.TRADED_WEEK]

    assert old != new
    assert data.team_at(fakes.TRADED_ID, 0) == old == data.team_at(fakes.TRADED_ID, 1)     # first game of the season
    assert data.team_at(fakes.TRADED_ID, fakes.TRADED_WEEK) == old       # not known until he has played there
    assert data.team_at(fakes.TRADED_ID, fakes.TRADED_WEEK + 1) == new
    assert data.team_at(fakes.TRADED_ID, 17) == new


def test_season_data_lookups_default_to_zero_and_none_for_unknown_players():
    data = fakes.fake_season()
    defense = fakes.player_id("DST", 1)

    assert data.team_at(999, 5) is None and not data.on_bye(999, 5)
    assert data.projection(999, 5) == 0.0 and data.points(999, 5) == 0.0
    assert len({team for _, team in data.team_by_week[defense]}) == 1
    assert data.points(fakes.BREAKOUT_ID, 1) == data.actual[(fakes.BREAKOUT_ID, 1)]


def test_patched_and_future_changed_copy_without_touching_the_original():
    data = fakes.fake_season()
    key = (fakes.BUST_ID, 3)

    edited = fakes.patched(data, projected={key: None}, actual={key: 50.0})
    later = fakes.future_changed(data, 6)

    assert key in data.projected and data.actual[key] != 50.0
    assert edited.projection(*key) == 0.0 and edited.points(*key) == 50.0
    assert all(later.actual[k] == v for k, v in data.actual.items() if k[1] <= 6)
    assert all(later.projected[k] == v for k, v in data.projected.items() if k[1] <= 6)
    assert all(later.actual[k] != v for k, v in data.actual.items() if k[1] > 6)
    assert all(later.projected.get(k) != v for k, v in data.projected.items() if k[1] > 6)
    assert {k: v for k, v in later.injuries.items() if k[1] <= 6} == {k: v for k, v in data.injuries.items() if k[1] <= 6}
    assert {k for k in later.injuries if k[1] > 6} == {(pid, w) for pid in (fakes.BREAKOUT_ID, fakes.BUST_ID)
                                                         for w in range(7, 18)}


# ---------------- as_of: the season with the future removed ----------------

@pytest.mark.parametrize("week", [0, 1, 6, 12, 17])
def test_as_of_keeps_only_what_was_known_before_the_week(week):
    data = fakes.fake_season()

    known = data.as_of(week)

    assert dict(known.actual) == {key: v for key, v in data.actual.items() if key[1] < week}
    assert dict(known.projected) == {key: v for key, v in data.projected.items() if key[1] <= max(1, week)}
    assert all(games == tuple(g for g in data.team_by_week[pid] if g[0] < week) or games == data.team_by_week[pid][:1]
               for pid, games in known.team_by_week.items())
    assert set(known.team_by_week) == set(data.team_by_week) & set(known.players)
    assert (known.known_before, data.known_before) == (week, None)
    assert known.players == {pid: p for pid, p in data.players.items()
                             if p.adp is not None or pid in {k[0] for k in (*known.actual, *known.projected)}}
    assert known.schedule is data.schedule and known.last_week == data.last_week
    assert dict(known.injuries) == {key: v for key, v in data.injuries.items() if key[1] <= week}


def test_as_of_holds_no_tags_at_draft_time_and_a_tag_never_adds_a_player():
    data = fakes.fake_season()
    outsider = fakes.player_id("K", 33)                  # not in the pool before his first game in week 9
    data = fakes.tagged(data, {(outsider, 2): fakes.Injury("O")})

    assert dict(data.as_of(0).injuries) == {} and data.as_of(0).projected == fakes.fake_season().as_of(0).projected
    assert outsider not in data.as_of(2).players and (outsider, 2) not in data.as_of(2).injuries
    assert data.as_of(3).injuries[fakes.QUESTIONABLE].tag == "Q" and fakes.QUESTIONABLE not in data.as_of(2).injuries


def test_fake_season_tag_story():
    data = fakes.fake_season()
    tags = data.injuries

    assert tags[fakes.QUESTIONABLE] == fakes.Injury("Q", "LP", "Hamstring")
    assert tags[fakes.DOUBTFUL] == fakes.Injury("D", "DNP", "Ankle")
    assert all(tags[(fakes.INJURED_STAR_ID, w)] == fakes.Injury("O", "DNP", "Knee") for w in fakes.INJURED_WEEKS)
    assert tags[(fakes.INJURED_STAR_ID, fakes.STAR_BACK_WEEK)].tag == "Q"
    assert [w for pid, w in tags if pid == fakes.IR_KICKER_ID] == list(range(9, 18))
    assert data.projection(fakes.IR_KICKER_ID, 9) == 0.0 and data.projection(fakes.IR_KICKER_ID, 8) > 0
    assert not any(pid in (fakes.NEVER_PLAYED_ID, fakes.player_id("K", 32)) for pid, _ in tags)
    assert sorted({w for _, w in tags}) == [3, 4, *range(6, 18)]
    assert {pid for pid, _ in fakes.fake_season(weeks=5).injuries} == {fakes.BREAKOUT_ID, fakes.TRADED_ID}
    assert fakes.Injury("SUS").hard and not fakes.Injury("Q").hard


def test_tagged_replaces_and_removes_tags_without_touching_the_original():
    data = fakes.fake_season()

    edited = fakes.tagged(data, {fakes.QUESTIONABLE: None, (fakes.BUST_ID, 2): fakes.Injury("PUP")})

    assert fakes.QUESTIONABLE in data.injuries and fakes.QUESTIONABLE not in edited.injuries
    assert edited.injuries[(fakes.BUST_ID, 2)].tag == "PUP" and (fakes.BUST_ID, 2) not in data.injuries
    assert edited.projected == data.projected


@pytest.mark.parametrize("week", [0, 1, 6, 12])
def test_as_of_answers_every_point_in_time_question_like_the_full_season(week):
    data = fakes.fake_season()
    known = data.as_of(week)

    for pid in known.players:                   # the pool known then; later arrivals are tested elsewhere
        for w in range(0, week + 1):
            assert known.team_at(pid, w) == data.team_at(pid, w) and known.on_bye(pid, w) == data.on_bye(pid, w)
        assert known.projection(pid, week) == data.projection(pid, week)
        assert known.points(pid, week) == 0.0 and known.projection(pid, max(1, week) + 1) == 0.0
    assert known.team_at(fakes.TRADED_ID, 17) == data.team_at(fakes.TRADED_ID, week)        # no later team


def test_as_of_is_cached_and_never_widens():
    data = fakes.fake_season()

    known = data.as_of(6)

    assert data.as_of(6) is known and data.as_of(7) is not known
    assert known.as_of(6) is known and known.as_of(9) is known          # already holds no more than that
    assert known.as_of(3).known_before == 3 and dict(known.as_of(3).actual) == dict(data.as_of(3).actual)
    assert fakes.fake_season(seed=2).as_of(6) is not known
    replay_pool.clear_cache()
    assert data.as_of(6) is not known
    with pytest.raises(ValueError):
        data.as_of(-1)


@pytest.mark.parametrize("week", [0, 1, 6, 12])
def test_as_of_is_identical_when_only_the_future_differs(week):
    data = fakes.fake_season()
    other = fakes.future_changed(data, after_week=max(1, week))

    known, altered = data.as_of(week), other.as_of(week)

    assert dict(other.projected) != dict(data.projected)
    assert dict(known.projected) == dict(altered.projected) and dict(known.team_by_week) == dict(altered.team_by_week)
    assert {k: v for k, v in altered.actual.items() if k[1] < week} == dict(known.actual)
    assert dict(other.injuries) != dict(data.injuries) and dict(known.injuries) == dict(altered.injuries)


# ---------------- a league just after its draft ----------------

@pytest.mark.parametrize("teams", [8, 10, 12, 14])
def test_drafted_state_gives_every_team_a_full_legal_roster(teams):
    settings = fakes.fake_settings(teams=teams, draft_slot=teams - 2)
    data = fakes.fake_season()

    state = fakes.drafted_state(settings)

    drafted = [pid for team in state.teams for pid in team.roster]
    assert [team.id for team in state.teams] == list(range(1, teams + 1)) == list(state.draft_order)
    assert [team.id for team in state.teams if team.is_user] == [settings.draft_slot]
    assert all(len(team.roster) == settings.roster_size for team in state.teams)
    assert len(set(drafted)) == len(drafted) == teams * settings.rounds and set(drafted) <= set(data.players)
    for team in state.teams:
        positions = [data.players[pid].position for pid in team.roster]
        assert replay_values.can_field_lineup(positions, settings)
        assert positions.count("K") >= 1 and positions.count("DST") >= 1
    assert fakes.NEVER_PLAYED_ID in drafted


@pytest.mark.parametrize("teams", [8, 12, 14])
def test_drafted_state_is_a_straight_adp_snake_ready_for_week_one(teams):
    settings, data = fakes.fake_settings(teams=teams), fakes.fake_season()
    by_adp = sorted((p for p in data.players.values() if p.adp is not None), key=lambda p: p.adp)

    state = fakes.drafted_state(settings)

    assert [pick.player_id for pick in state.picks[:teams * 2]] == [p.player_id for p in by_adp[:teams * 2]]
    assert [pick.team_id for pick in state.picks[:teams * 2]] == [*range(1, teams + 1), *range(teams, 0, -1)]
    assert [(pick.overall, pick.round) for pick in state.picks[teams - 1:teams + 1]] == [(teams, 1), (teams + 1, 2)]
    assert all(pick.player_id in replay_state.team(state, pick.team_id).roster for pick in state.picks)
    assert (state.phase, state.week, state.results, state.claims, state.locked) == ("open", 1, (), (), ())
    assert state.lineup is None and state.offers == () and dict(state.traded) == {}
    assert state.waiver_order == tuple(range(teams, 0, -1))
    assert len(state.schedule) == settings.regular_season_weeks
    assert all(sorted(t for game in week for t in game) == list(range(1, teams + 1)) for week in state.schedule)


def test_drafted_state_is_deterministic_cached_and_does_not_need_the_draft_module():
    settings = fakes.fake_settings()

    first, again = fakes.drafted_state(), fakes.drafted_state(settings, fakes.fake_season(), 0)
    other = fakes.drafted_state(seed=1)

    assert again is first
    assert other.teams == first.teams and other.schedule != first.schedule      # the seed only sets the schedule
    assert fakes.drafted_state(data=fakes.fake_season(seed=5)).picks != first.picks
    assert not hasattr(fakes, "replay_draft")
    assert replay_state.state_from_json(replay_state.to_json(first)) == first


def test_drafted_state_does_not_depend_on_what_happens_after_the_draft():
    data = fakes.fake_season()

    real, altered = fakes.drafted_state(data=data), fakes.drafted_state(data=fakes.future_changed(data, after_week=1))

    assert altered is not real and altered.picks == real.picks and altered.teams == real.teams


# ---------------- loading from the database ----------------

@pytest.fixture(scope="module")
def seeded_db():
    """One database holding the fake season, shared by the read-only tests below."""
    engine = _fresh_engine()
    with Session(engine) as session:
        fakes.seed_db(session, fakes.fake_season())
        yield session
    engine.dispose()


@pytest.fixture(scope="module")
def loaded(seeded_db) -> SeasonData:
    replay_pool.clear_cache()
    return replay_pool.load_season_data(seeded_db, fakes.SEASON, "half")


@pytest.fixture
def small_db():
    """A private database for tests that change rows."""
    engine = _fresh_engine()
    with Session(engine) as session:
        fakes.seed_db(session, fakes.fake_season())
        yield session
    engine.dispose()


def test_load_returns_the_same_pool_with_adp(loaded):
    data = fakes.fake_season()

    assert dict(loaded.players) == dict(data.players)
    assert (loaded.season, loaded.scoring, loaded.last_week) == (data.season, "half", 17)


def test_load_returns_the_same_real_points_for_skill_kickers_and_defenses(loaded):
    data = fakes.fake_season()

    assert set(loaded.actual) == set(data.actual)
    assert all(loaded.actual[key] == pytest.approx(points, abs=1e-9) for key, points in data.actual.items())
    assert {data.players[pid].position for pid, _ in loaded.actual} == set(ALL_POSITIONS)


def test_load_scores_kickers_and_defenses_with_yahoo_default_scoring(loaded, seeded_db):
    kick = seeded_db.scalars(select(KickerGame).where(KickerGame.made_0_39 > 0, KickerGame.pat_made > 0)).first()
    stop = seeded_db.scalars(select(TeamGameStats).where(TeamGameStats.def_sacks > 0)).first()
    defense_id = seeded_db.scalar(select(Player.id).where(Player.gsis_id == f"DST-{stop.team}"))

    assert loaded.actual[(kick.player_id, kick.week)] == 3 * kick.made_0_39 + kick.pat_made
    assert loaded.actual[(kick.player_id, kick.week)] == kdst_model._actual_k(kick, kdst_model.DEFAULT_K)
    assert loaded.actual[(defense_id, stop.week)] == kdst_model._actual_dst(stop, kdst_model.DEFAULT_DST)
    assert loaded.actual[(defense_id, stop.week)] == stop.def_sacks + kdst_model.pa_points(stop.points_against,
                                                                                         kdst_model.DEFAULT_DST)


def test_load_prefers_espn_and_fills_gaps_and_zeros_from_sleeper(loaded, seeded_db):
    data = fakes.fake_season()
    rows = {(r.player_id, r.week, r.source): r.pts_half for r in seeded_db.scalars(select(ReplayProjection))}
    both = next(k for k in data.projected if (k[0], k[1], "espn") in rows and rows[(k[0], k[1], "espn")] > 0)
    missing = next(k for k in data.projected if (k[0], k[1], "espn") not in rows)
    zero = next(k for k in data.projected if rows.get((k[0], k[1], "espn")) == 0.0)

    assert set(loaded.projected) == set(data.projected)
    assert all(loaded.projected[key] == pytest.approx(value, abs=1e-9) for key, value in data.projected.items())
    assert rows[(*both, "sleeper")] == pytest.approx(rows[(*both, "espn")] + fakes.SLEEPER_DECOY)
    assert loaded.projected[both] == rows[(*both, "espn")]
    assert loaded.projected[missing] == rows[(*missing, "sleeper")]
    assert loaded.projected[zero] == rows[(*zero, "sleeper")] > 0


def test_load_returns_the_same_teams_byes_and_schedule(loaded):
    data = fakes.fake_season()

    assert dict(loaded.team_by_week) == dict(data.team_by_week)
    assert dict(loaded.schedule.opponents) == dict(data.schedule.opponents)
    assert set(loaded.schedule.games) == set(data.schedule.games)
    assert loaded.team_at(fakes.TRADED_ID, fakes.TRADED_WEEK) == data.team_at(fakes.TRADED_ID, fakes.TRADED_WEEK)
    assert loaded.team_at(fakes.TRADED_ID, 17) == data.team_at(fakes.TRADED_ID, 17)
    assert loaded.on_bye(fakes.INJURED_STAR_ID, 12) and not loaded.on_bye(fakes.INJURED_STAR_ID, 6)
    assert all(loaded.on_bye(pid, week) == data.on_bye(pid, week) for pid in data.players for week in (5, 9, 12))


def test_load_uses_the_requested_scoring_for_points_and_projections(loaded, seeded_db):
    key = next(k for k in loaded.actual if k[0] > fakes.ID_BASE["WR"] and k[0] < fakes.ID_BASE["TE"]
               and loaded.actual[k] >= 3 and loaded.projected[k] > 2 and (k[0] + k[1]) % 7)
    passer = next(k for k in loaded.actual if k[0] < fakes.ID_BASE["RB"])
    kicker = next(k for k in loaded.projected if fakes.ID_BASE["K"] < k[0] < fakes.ID_BASE["DST"] and (k[0] + k[1]) % 7)

    full, standard = (replay_pool.load_season_data(seeded_db, fakes.SEASON, s) for s in ("ppr", "std"))

    assert full.actual[key] == pytest.approx(loaded.actual[key] + 1.0)           # two catches
    assert standard.actual[key] == pytest.approx(loaded.actual[key] - 1.0)
    assert full.actual[passer] == pytest.approx(loaded.actual[passer])           # no catches
    assert full.projected[key] == pytest.approx(loaded.projected[key] + 1.0)
    assert standard.projected[key] == pytest.approx(loaded.projected[key] - 1.0)
    assert full.projected[kicker] == loaded.projected[kicker] == standard.projected[kicker]
    assert (full.scoring, standard.scoring) == ("ppr", "std")


def test_load_is_cached_until_the_cache_is_cleared(seeded_db):
    first = replay_pool.load_season_data(seeded_db, fakes.SEASON, "half")
    again = replay_pool.load_season_data(seeded_db, fakes.SEASON, "half")
    replay_pool.clear_cache()
    rebuilt = replay_pool.load_season_data(seeded_db, fakes.SEASON, "half")

    assert again is first
    assert rebuilt is not first and dict(rebuilt.actual) == dict(first.actual)


def test_readiness_reports_a_loaded_season_as_ready(seeded_db):
    assert replay_pool.readiness(seeded_db, fakes.SEASON) == {
        "season": fakes.SEASON, "ready": True, "has_stats": True, "has_schedule": True, "has_kdst": True,
        "adp_players": 220, "projection_weeks": 17, "kickers": 32, "defenses": 32, "injury_weeks": 14,
        "expert_snapshots": 18}
    assert replay_pool.ready_seasons(seeded_db) == [fakes.SEASON]


def test_a_season_without_injury_tags_is_still_ready_and_loads_with_none(small_db):
    small_db.execute(delete(ReplayInjury))
    small_db.commit()

    status = replay_pool.readiness(small_db, fakes.SEASON)

    assert status["ready"] is True and status["injury_weeks"] == 0
    assert dict(replay_pool.load_season_data(small_db, fakes.SEASON, "half").injuries) == {}


def test_load_returns_the_same_injury_tags(loaded):
    data = fakes.fake_season()

    assert dict(loaded.injuries) == dict(data.injuries) != {}
    assert loaded.injuries[fakes.QUESTIONABLE] == fakes.Injury("Q", "LP", "Hamstring")


def test_an_unloaded_season_is_not_ready_and_cannot_be_loaded(seeded_db):
    status = replay_pool.readiness(seeded_db, 2019)

    assert status == {"season": 2019, "ready": False, "has_stats": False, "has_schedule": False,
                      "has_kdst": False, "adp_players": 0, "projection_weeks": 0, "kickers": 0, "defenses": 0,
                      "injury_weeks": 0, "expert_snapshots": 0}
    with pytest.raises(NoDataError):
        replay_pool.load_season_data(seeded_db, 2019, "half")


def test_load_rejects_an_unknown_scoring(seeded_db):
    with pytest.raises(ValueError):
        replay_pool.load_season_data(seeded_db, fakes.SEASON, "superflex")


def test_a_missing_defense_player_makes_the_season_not_ready(small_db):
    small_db.execute(delete(Player).where(Player.gsis_id == "DST-BUF"))
    small_db.commit()

    status = replay_pool.readiness(small_db, fakes.SEASON)

    assert status["has_kdst"] is False and status["ready"] is False
    with pytest.raises(NoDataError):
        replay_pool.load_season_data(small_db, fakes.SEASON, "half")


@pytest.mark.parametrize("missing", [(), (17,), (11,), (1,)])
def test_a_season_needs_projections_for_every_regular_season_week(small_db, missing):
    small_db.execute(delete(ReplayProjection).where(ReplayProjection.week.in_(missing)))
    small_db.commit()

    status = replay_pool.readiness(small_db, fakes.SEASON)

    assert status["projection_weeks"] == 17 - len(missing)
    assert status["ready"] is (not missing)                  # one hole zeroes every player that week
    assert replay_pool.ready_seasons(small_db) == ([] if missing else [fakes.SEASON])
    assert not hasattr(replay_pool, "MIN_PROJECTION_WEEKS")


@pytest.mark.parametrize("kept, ready", [(179, False), (180, True)])
def test_a_season_needs_adp_for_a_full_draft(small_db, kept, ready):
    ranked = small_db.scalars(select(ReplayAdp.id).order_by(ReplayAdp.adp)).all()
    small_db.execute(delete(ReplayAdp).where(ReplayAdp.id.in_(ranked[kept:])))
    small_db.commit()

    status = replay_pool.readiness(small_db, fakes.SEASON)

    assert status["adp_players"] == kept and status["ready"] is ready


@pytest.mark.parametrize("position, key", [("K", "kickers"), ("DST", "defenses")])
def test_a_season_needs_fourteen_kickers_and_defenses_with_an_adp_or_a_week_one_projection(small_db, position, key):
    ids = [fakes.player_id(position, rank) for rank in range(1, fakes.POOL[position] + 1)]
    with_adp = set(small_db.scalars(select(ReplayAdp.player_id).where(ReplayAdp.player_id.in_(ids))))
    spare = [pid for pid in ids if pid not in with_adp]                  # known only from week-1 projections
    week_one = (ReplayProjection.week == 1, ReplayProjection.season == fakes.SEASON)

    small_db.execute(delete(ReplayProjection).where(*week_one, ReplayProjection.player_id.in_(spare[2:])))
    small_db.commit()
    enough = replay_pool.readiness(small_db, fakes.SEASON)
    small_db.execute(delete(ReplayProjection).where(*week_one, ReplayProjection.player_id.in_(spare[1:])))
    small_db.commit()
    short = replay_pool.readiness(small_db, fakes.SEASON)

    assert len(with_adp) == 12
    assert (enough[key], enough["ready"]) == (14, True) and (short[key], short["ready"]) == (13, False)
    assert replay_pool.MIN_KICKERS == replay_pool.MIN_DEFENSES == 14


def test_ready_seasons_is_empty_without_data():
    with Session(_fresh_engine()) as session:
        assert replay_pool.ready_seasons(session) == []


def test_load_ignores_postseason_weeks_null_defense_scores_and_other_positions(small_db):
    data = fakes.fake_season()
    passer, defense = fakes.player_id("QB", 1), fakes.player_id("DST", 1)
    team = data.team_at(defense, 1)
    week = data.team_by_week[defense][0][0]
    small_db.execute(insert(Player), [{"id": 20_001, "gsis_id": "00-LB", "full_name": "A Linebacker", "position": "LB"}])
    small_db.execute(insert(PlayerStatsWeekly), [
        {"player_id": passer, "season": fakes.SEASON, "week": 18, "season_type": "REG", "rushing_yards": 400},
        {"player_id": passer, "season": fakes.SEASON, "week": 19, "season_type": "POST", "rushing_yards": 400}])
    small_db.execute(insert(ReplayProjection), [
        {"season": fakes.SEASON, "week": w, "player_id": pid, "source": "espn", "pts_ppr": 9.0, "pts_half": 9.0,
         "pts_std": 9.0} for w, pid in ((18, passer), (3, 20_001))])
    small_db.query(TeamGameStats).filter_by(team=team, week=week).update({"points_against": None})
    small_db.query(NflGame).update({"home_score": 31, "away_score": 3})
    small_db.commit()

    loaded = replay_pool.load_season_data(small_db, fakes.SEASON, "half")

    assert loaded.last_week == 17 and max(week for _, week in (*loaded.actual, *loaded.projected)) == 17
    assert 20_001 not in loaded.players and (20_001, 3) not in loaded.projected
    assert (defense, week) not in loaded.actual                       # not scored as a shutout
    assert dict(loaded.team_by_week[defense])[week] == team           # but it is still that team's game
    assert all(game.home_score is None and game.away_score is None for game in loaded.schedule.games)   # no results


def test_only_pool_players_tags_inside_the_season_are_loaded(small_db):
    outsider = Player(full_name="Line Backer", position="LB")                 # tagged, but not in the pool
    small_db.add(outsider)
    small_db.flush()
    small_db.add_all([ReplayInjury(season=fakes.SEASON, week=1, player_id=outsider.id, tag="IR"),
                      ReplayInjury(season=fakes.SEASON, week=18, player_id=fakes.BUST_ID, tag="O")])    # past week 17
    small_db.commit()
    replay_pool.clear_cache()

    assert dict(replay_pool.load_season_data(small_db, fakes.SEASON, "half").injuries) == dict(fakes.fake_season().injuries)
