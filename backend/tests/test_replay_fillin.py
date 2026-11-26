"""replay_fillin: who is filling in at QB (point in time), the share he is expected to start, and his chart."""
from dataclasses import replace
from types import MappingProxyType

import pytest

from services import replay_chart, replay_fillin, replay_news, replay_values as rv
from services.replay_box import BoxLine
from services.replay_pool import hard_tag_returns
from tests import replay_fakes as fakes

OUT = (5, 6)                     # the regular starter's hard-tagged weeks in `_season`


@pytest.fixture(scope="module")
def data():
    return fakes.fake_season()


def _team_qbs(data):
    qbs = sorted((pid for pid, p in data.players.items() if p.position == "QB"), key=lambda pid: data.players[pid].adp or 999)
    regular = qbs[0]
    team = data.team_at(regular, 1)
    return team, regular, next(pid for pid in qbs[1:] if data.team_at(pid, 1) == team)


def _season(data, backup_weeks, tagged=OUT, regular_plays=None):
    """The regular passes in his team's games except `backup_weeks`, where the backup does; tagged O in `tagged`."""
    team, regular, backup = _team_qbs(data)
    played = [w for w, _ in data.team_by_week[regular]]
    regular_plays = [w for w in played if w not in backup_weeks] if regular_plays is None else regular_plays
    box = {k: v for k, v in data.box.items() if k[0] not in (regular, backup)}
    box.update({(regular, w): BoxLine(pass_yds=250) for w in regular_plays})
    box.update({(backup, w): BoxLine(pass_yds=230) for w in backup_weeks})
    games = {**data.team_by_week, regular: tuple((w, team) for w in regular_plays),
             backup: tuple(sorted({*((w, team) for w in backup_weeks)}))}
    actual = {k: v for k, v in data.actual.items() if k[0] not in (regular, backup)}
    actual.update({(regular, w): 18.0 for w in regular_plays})
    actual.update({(backup, w): 15.0 for w in backup_weeks})
    projected = {**data.projected, **{(backup, w): 14.0 for w in range(1, data.last_week + 1)}}
    injuries = {k: v for k, v in data.injuries.items() if k[0] not in (regular, backup)}
    injuries.update({(regular, w): fakes.Injury("O") for w in tagged})
    season = replace(data, box=MappingProxyType(box), team_by_week=MappingProxyType(games),
                     actual=MappingProxyType(actual), injuries=MappingProxyType(injuries),
                     projected=MappingProxyType(projected))
    return replace(season, returns=MappingProxyType(hard_tag_returns(season.injuries, season.actual, season.last_week))), \
        team, regular, backup


def _at(season, week):
    return dict(replay_fillin.fill_ins(season.as_of(week), week))


def test_the_backup_fills_in_while_the_regular_is_out_and_hands_back_when_he_returns(data):
    season, team, regular, backup = _season(data, OUT)
    assert _at(season, 5) == {backup: replay_fillin.FillIn(team, regular, 0, "out")}      # before his first start
    assert _at(season, 6) == {backup: replay_fillin.FillIn(team, regular, 1, "out")}
    games = [w for w in range(OUT[-1] + 1, season.last_week + 1) if season.schedule.opponent(team, w)]
    back, after = games[0], games[1]                                       # the regular's first game back, the next
    assert _at(season, back) == {backup: replay_fillin.FillIn(team, regular, 2, "back")}
    assert backup not in _at(season, after)                                # the regular started again: over


def test_a_backup_who_takes_the_job_is_a_fill_in_until_his_probation_ends(data):
    probation = int(replay_fillin.stored()["probation"])
    weeks = [w for w, _ in data.team_by_week[_team_qbs(data)[1]] if w >= 5]
    season, team, regular, backup = _season(data, weeks, tagged=())          # the regular healthy, not playing
    started = [w for w in weeks if w < weeks[probation]]
    assert _at(season, weeks[1]) == {backup: replay_fillin.FillIn(team, regular, 1, "taken")}
    assert _at(season, weeks[probation - 1])[backup].starts == len(started) - 1
    assert backup not in _at(season, weeks[probation])                         # the team's QB now


def test_probation_ends_only_when_the_regular_is_not_expected_back(data):
    probation = int(replay_fillin.stored()["probation"])
    fill = replay_fillin.FillIn("KC", 1, probation, "out")
    games = [14, 15, 16, 17]
    season_ending = replay_news.Report(9, None, "Out for the season.")
    assert replay_fillin.share(fill, season_ending, games) is None
    named = replay_news.Report(9, 16, "Expected back week 16.")
    shares = replay_fillin.stored()["shares"]
    assert replay_fillin.share(fill, named, games) == pytest.approx(min(shares["named"][-1], 0.5 + 0.5 * shares["keep"][-1]))
    early = replay_fillin.FillIn("KC", 1, 1, "out")
    assert replay_fillin.share(early, season_ending, games) == pytest.approx(shares["season"][1])


def test_the_definition_reads_nothing_from_the_week_on(data):
    season, *_ = _season(data, OUT)
    for week in (5, 6, 7, 8):
        assert _at(season, week) == _at(fakes.future_changed(season, week), week)     # tags of `week` are known


def test_a_fill_ins_chart_is_his_share_of_a_starters_value_without_the_form_bonus(data, monkeypatch):
    season, team, regular, backup = _season(data, OUT)
    league = rv.league_settings(fakes.fake_settings())
    rv.clear_cache()
    priced = rv.knowledge(season, league, 6, 16, 0).by_id[backup]
    monkeypatch.setattr(replay_fillin, "fill_ins", lambda data, week: MappingProxyType({}))
    rv.clear_cache()
    plain = rv.knowledge(season, league, 6, 16, 0).by_id[backup]
    rv.clear_cache()
    assert priced.fill_in is not None and 0 < priced.fill_in < 1 and plain.fill_in is None
    curve = replay_chart.slot_curve(season.as_of(6))
    backup_value = replay_chart.value_at(curve, "QB", 33)
    rank_value = plain.healthy_chart - plain.form_chart
    expected = min(plain.chart, priced.fill_in * rank_value + (1 - priced.fill_in) * min(backup_value, rank_value))
    assert priced.chart == pytest.approx(expected, abs=0.1) and priced.form_chart == 0.0
    assert priced.chart <= plain.chart
