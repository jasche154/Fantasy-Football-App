"""Review of #66, round 3, item 4: small fixture tests for logic the season tests only reach indirectly."""
from datetime import datetime, timezone

import numpy as np
import pytest

from database.models import DepthChartEntry, Player, PlayerStatus
from services import replay_fillin, sim_draw, sim_game, sim_qb, sim_setup, sim_world
from services.replay_pool import Injury
from tests import replay_fakes as fakes
from tests import test_replay_fillin as fillin_tests

WEEK = 9


@pytest.fixture(scope="module")
def belief():
    return sim_world.estimate(fakes.fake_season(), WEEK)


def test_a_busy_passing_day_gets_shorter_catches(belief):
    """`game.ypc_volume`: yards per catch at twice the side's usual targets are below those at half."""
    team = sorted(belief.teams)[0]
    side = sim_game._side_of(belief, team, None, set())
    def per_catch(n_targets: int) -> float:
        rng, catches, yards = np.random.default_rng(1), 0, 0
        for _ in range(300):
            o = sim_draw._offense(side, n_targets, 25, 1.0, rng)
            catches, yards = catches + int(o.catches.sum()), yards + int(o.rec_yds.sum())
        return yards / catches
    usual = round(side.targets)
    assert per_catch(2 * usual) < per_catch(max(1, usual // 2)) * 0.85            # 4x volume ** -0.3 ~ 0.66


def _player(session, name: str, gsis: str | None = None, position: str = "QB") -> Player:
    player = Player(full_name=name, position=position, nfl_team="KC", gsis_id=gsis)
    session.add(player)
    session.flush()
    return player


def test_qb_inputs_reads_young_first_rounders_and_this_seasons_picks(session):
    young, old, second, rookie, receiver = (_player(session, n, f"00-{n}") for n in ("Young", "Old", "Second", "Rookie", "Wideout"))
    picks = [{"season": 2025, "pick": 12, "position": "QB", "gsis_id": "00-Young"},
             {"season": 2022, "pick": 3, "position": "QB", "gsis_id": "00-Old"},          # outside the window
             {"season": 2026, "pick": 40, "position": "QB", "gsis_id": "00-Second"},      # not a first-rounder
             {"season": 2026, "pick": 7, "position": "QB", "gsis_id": "00-Rookie"},
             {"season": 2026, "pick": 2, "position": "WR", "gsis_id": "00-Wideout"},
             {"season": 2026, "pick": 9, "position": "QB", "gsis_id": "00-Nobody"}]       # no player row
    inputs = sim_setup.qb_inputs(session, 2026, picks)
    assert dict(inputs.young) == {young.id: 2025, rookie.id: 2026}
    assert dict(inputs.picks) == {second.id: 40, rookie.id: 7}
    assert old.id not in inputs.young and receiver.id not in inputs.picks


def test_qb_rooms_reads_the_depth_chart_else_sleeper(session):
    starter, backup, other, sleeper_one, sleeper_two = (_player(session, n) for n in ("Starter", "Backup", "Other", "S1", "S2"))
    session.add_all([DepthChartEntry(team="KC", player_id=backup.id, position="QB", depth=2),
                     DepthChartEntry(team="KC", player_id=starter.id, position="QB", depth=1),
                     DepthChartEntry(team="KC", player_id=other.id, position="WR", depth=1)])
    now = datetime.now(timezone.utc)
    session.add_all([PlayerStatus(player_id=sleeper_two.id, team="BUF", depth_position="QB", depth_order=2, fetched_at=now),
                     PlayerStatus(player_id=sleeper_one.id, team="BUF", depth_position="QB", depth_order=1, fetched_at=now),
                     PlayerStatus(player_id=other.id, team="KC", depth_position="QB", depth_order=3, fetched_at=now)])
    session.flush()
    rooms = sim_setup.qb_rooms(session)
    assert rooms["KC"] == [(starter.id, "Starter"), (backup.id, "Backup")]          # the chart wins over Sleeper
    assert rooms["BUF"] == [(sleeper_one.id, "S1"), (sleeper_two.id, "S2")]


def test_preseason_ranks_take_the_better_of_value_and_market(belief):
    plain = sim_qb.preseason_ranks(belief)
    worst = next(pid for pid, rank in plain.items() if rank == 10)          # a starter the market prices
    record = {worst: 60.0}                                  # a monster last season: the market ranks him first
    ranked = sim_qb.preseason_ranks(belief, record)
    assert ranked[worst] == 1 and all(ranked[pid] <= plain[pid] for pid in plain if pid != worst)


def test_without_news_keeps_a_fill_in_until_a_season_tag_and_probation_say_otherwise():
    data = fakes.fake_season()
    team, regular, backup = fillin_tests._team_qbs(data)
    probation = int(replay_fillin.stored()["probation"])
    weeks = [w for w, _ in data.team_by_week[regular] if w >= 4][:probation + 2]
    for tag, kept in (("O", True), ("IR", False)):
        season, *_ = fillin_tests._season(data, weeks, tagged=weeks)
        season = fillin_tests._with(season, injuries={(regular, w): Injury(tag) for w in weeks})
        late = weeks[-1]
        assert backup in replay_fillin.without_news(season.as_of(weeks[1]), weeks[1])       # early: a fill-in
        assert (backup in replay_fillin.without_news(season.as_of(late), late)) is kept      # past probation
