"""Replay League: who is in the pool, and on which team, as known at a week (no hindsight)."""
from dataclasses import replace
from types import MappingProxyType

import pytest
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from database.models import Base, ReplayAdp
from services import replay_draft, replay_game as rg, replay_pool, replay_views as views
from services import replay_views_league as league
from services.replay_pool import PoolPlayer
from tests import replay_fakes as fakes

DATA = fakes.fake_season()
LATE = fakes.player_id("K", fakes.KICKER_CHANGES[0][1])        # no ADP, nothing before his first week
FIRST = min(week for pid, week in (*DATA.projected, *DATA.actual) if pid == LATE)
GHOST = fakes.NEVER_PLAYED_ID


def _game(state, settings) -> views.Game:
    return views.Game(1, "Test league", settings, state, DATA, "2026-01-01T00:00:00")


# ---------------- pool membership (finding 9) ----------------

def test_a_late_arrival_without_an_adp_is_not_in_the_pool_before_his_first_projection():
    assert DATA.players[LATE].adp is None and FIRST >= fakes.KICKER_CHANGES[0][2]
    for week in range(0, FIRST):
        known = DATA.as_of(week)
        assert LATE not in known.players and LATE not in known.team_by_week
        assert LATE not in fakes.fake_knowledge(week).by_id
    for week in (FIRST, FIRST + 1, 17):
        assert LATE in DATA.as_of(week).players and LATE in fakes.fake_knowledge(week).by_id


def test_the_draft_board_lists_only_players_known_at_draft_time():
    settings = fakes.fake_settings()
    drafting = rg.create_game(settings, DATA, 0)

    board = {p.player_id for p in replay_draft.available(drafting, DATA)}

    assert LATE not in board and GHOST in board                 # an ADP is enough
    assert all(p.adp is not None or DATA.projection(pid, 1) > 0 for pid, p in DATA.as_of(0).players.items())


@pytest.mark.parametrize("week", [0, 1, 6, 12])
def test_who_appears_later_does_not_change_the_pool_known_at_a_week(week):
    newcomer = PoolPlayer(99_999, "Late Signing", "RB")
    later = max(1, week) + 1
    other = fakes.future_changed(DATA, after_week=max(1, week))
    other = replace(other, players=MappingProxyType({**other.players, newcomer.player_id: newcomer}),
                    projected=MappingProxyType({**other.projected, (newcomer.player_id, later): 9.0}))

    assert dict(other.as_of(week).players) == dict(DATA.as_of(week).players)
    assert newcomer.player_id in other.as_of(later).players


def test_the_pool_only_grows_with_the_week():
    pools = [set(DATA.as_of(week).players) for week in range(0, 18)]

    assert all(before <= after for before, after in zip(pools, pools[1:]))
    assert pools[-1] == set(DATA.players)


def test_the_player_page_of_a_late_arrival_lists_only_weeks_since_he_arrived():
    settings = fakes.fake_settings()
    state = replace(fakes.drafted_state(settings), week=FIRST + 1)

    page = league.player(_game(state, settings), LATE)

    assert [row["week"] for row in page["weeks"]] == [FIRST, FIRST + 1]


# ---------------- a drafted player who never plays (findings 7 and 8) ----------------

def test_a_drafted_player_who_never_plays_has_his_adp_team_and_its_bye():
    team = DATA.players[GHOST].adp_team
    bye = next(week for week in fakes.BYE_WEEKS if DATA.schedule.opponent(team, week) is None)

    assert team in fakes.TEAMS and GHOST not in DATA.team_by_week
    assert [DATA.as_of(w).team_at(GHOST, w) for w in (0, 1, 10)] == [team] * 3
    assert DATA.on_bye(GHOST, bye) and not DATA.on_bye(GHOST, 1)


@pytest.mark.parametrize("week", [0, 1, 4, 10])
def test_a_drafted_player_who_never_plays_has_the_games_left_of_his_teammates(week):
    team = DATA.players[GHOST].adp_team
    know = fakes.fake_knowledge(week)
    mate = next(v for v in know.values if v.team == team and v.player_id != GHOST)

    assert know.by_id[GHOST].team == team and know.by_id[GHOST].games_left == mate.games_left


def test_team_at_ignores_the_teams_of_later_games():
    week = fakes.TRADED_WEEK
    games = DATA.team_by_week[fakes.TRADED_ID]
    moved = tuple((w, "ZZZ" if w >= week else t) for w, t in games)
    other = replace(DATA, team_by_week=MappingProxyType({**DATA.team_by_week, fakes.TRADED_ID: moved}))

    assert other.team_at(fakes.TRADED_ID, week) == DATA.team_at(fakes.TRADED_ID, week) != "ZZZ"
    assert other.as_of(week).team_at(fakes.TRADED_ID, week) == DATA.team_at(fakes.TRADED_ID, week)


def _loaded(adp_team: str | None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as session:
            fakes.seed_db(session, DATA)
            session.execute(update(ReplayAdp).where(ReplayAdp.player_id == GHOST).values(team=adp_team))
            session.commit()
            replay_pool.clear_cache()
            return replay_pool.load_season_data(session, DATA.season, DATA.scoring)
    finally:
        replay_pool.clear_cache()
        engine.dispose()


def test_the_adp_team_round_trips_through_the_database_only_when_it_is_a_schedule_team():
    team = DATA.players[GHOST].adp_team

    kept, unknown, free = _loaded(team), _loaded("XYZ"), _loaded("FA")

    assert kept.players[GHOST].adp_team == team and kept.team_at(GHOST, 5) == team
    assert unknown.players[GHOST].adp_team is None and unknown.team_at(GHOST, 5) is None
    assert free.team_at(GHOST, 5) is None and not any(free.on_bye(GHOST, w) for w in range(1, 18))
