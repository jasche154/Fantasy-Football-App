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
    probation, graduated = int(replay_fillin.stored()["probation"]), replay_fillin.GRADUATED
    weeks = [w for w, _ in data.team_by_week[_team_qbs(data)[1]] if w >= 5]
    season, team, regular, backup = _season(data, weeks, tagged=())          # the regular healthy, not playing
    started = [w for w in weeks if w < weeks[probation]]
    assert _at(season, weeks[1]) == {backup: replay_fillin.FillIn(team, regular, 1, "taken")}
    assert _at(season, weeks[probation - 1])[backup].starts == len(started) - 1
    graduate = _at(season, weeks[probation])[backup]                            # the team's QB now, still climbing
    assert graduate.starts == probation and replay_fillin.graduated(graduate, None)
    assert _at(season, weeks[probation + graduated - 1])[backup].starts == probation + graduated - 1
    assert backup not in _at(season, weeks[probation + graduated])              # a starter's full value


def test_a_graduates_share_climbs_and_a_drafted_qb_climbs_no_slower_than_a_career_backup():
    probation, fitted = int(replay_fillin.stored()["probation"]), replay_fillin.stored()["graduated"]
    games = [14, 15, 16]
    for prior in (True, False):
        found = [replay_fillin.share(replay_fillin.FillIn("KC", 1, probation + n, "taken", prior), None, games)
                 for n in range(replay_fillin.GRADUATED + 1)]
        assert found[:-1] == [pytest.approx(v) for v in fitted["prior" if prior else "none"]] and found[-1] is None
        assert all(0 < a <= b < 1 for a, b in zip(found[:-1], found[1:-1]))           # it only climbs
    assert all(p >= n for p, n in zip(fitted["prior"], fitted["none"]))
    season_ending = replay_news.Report(9, None, "Out for the season.")
    out = replay_fillin.FillIn("KC", 1, probation, "out", False)
    assert replay_fillin.share(out, season_ending, games) == pytest.approx(fitted["none"][0])
    named = replay_news.Report(9, 16, "Expected back week 16.")
    assert not replay_fillin.graduated(out, named)                    # his regular expected back: still a fill-in


def test_the_climb_pools_a_later_bucket_that_falls_below_an_earlier_one():
    assert replay_fillin._climb([(9, 10), (6, 10), (9, 10)]) == pytest.approx([0.75, 0.75, 0.9])
    assert replay_fillin._climb([(5, 10), (8, 10)]) == pytest.approx([0.5, 0.8])


def test_probation_ends_only_when_the_regular_is_not_expected_back(data):
    probation = int(replay_fillin.stored()["probation"])
    fill = replay_fillin.FillIn("KC", 1, probation, "out")
    games = [14, 15, 16, 17]
    season_ending = replay_news.Report(9, None, "Out for the season.")
    assert replay_fillin.share(fill, season_ending, games) == pytest.approx(replay_fillin.stored()["graduated"]["none"][0])
    done = replay_fillin.FillIn("KC", 1, probation + replay_fillin.GRADUATED, "out")
    assert replay_fillin.share(done, season_ending, games) is None
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
    monkeypatch.setattr(replay_fillin, "_fill_ins", lambda data, week, teams: MappingProxyType({}))
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


def _with(season, box=None, games=None, injuries=None):
    season = replace(season, box=MappingProxyType({**season.box, **(box or {})}),
                     team_by_week=MappingProxyType({**season.team_by_week, **(games or {})}),
                     injuries=MappingProxyType({**season.injuries, **(injuries or {})}))
    return replace(season, returns=MappingProxyType(hard_tag_returns(season.injuries, season.actual, season.last_week)))


def test_a_starter_who_left_a_game_hurt_keeps_the_job_and_his_backup_fills_in(data):
    """Review item 1: the regular played week 4 (left early: the backup led the passing), then is tagged O."""
    played = [w for w, _ in data.team_by_week[_team_qbs(data)[1]]]
    season, team, regular, backup = _season(data, (4, *OUT), regular_plays=[w for w in played if w not in OUT])
    season = _with(season, box={(regular, 4): BoxLine(pass_yds=40)})
    assert _at(season, 5) == {backup: replay_fillin.FillIn(team, regular, 1, "out")}


def test_a_fill_in_past_probation_is_still_one_the_week_his_regular_returns(data):
    """Review item 2: no probation in the "back" state."""
    team, regular, _ = _team_qbs(data)
    games = [w for w, _ in data.team_by_week[regular] if w >= 4]
    run = games[:int(replay_fillin.stored()["probation"]) + 2]
    season, team, regular, backup = _season(data, run, tagged=run)
    back = games[len(run)]
    assert _at(season, back) == {backup: replay_fillin.FillIn(team, regular, len(run), "back")}


def test_a_regular_traded_away_hands_the_job_on_without_a_taking(data):
    """Review item 5: the regular plays for another team from week 6; his old team's new starter is its QB."""
    team, regular, _ = _team_qbs(data)
    later = [w for w, _ in data.team_by_week[regular] if w >= 6]
    season, team, regular, backup = _season(data, later, tagged=())
    other = next(t for t, _ in season.schedule.opponents if t != team)
    season = _with(season, games={regular: tuple((w, team) for w, _ in data.team_by_week[regular] if w < 6)
                                   + tuple((w, other) for w in later)},
                   box={(regular, w): BoxLine(pass_yds=260) for w in later},
                   injuries={(regular, later[2]): fakes.Injury("O")})
    for week in later[1:4]:
        assert backup not in _at(season, week) and regular not in _at(season, week)


def test_on_the_teams_bye_the_fill_in_keeps_last_weeks_state(data):
    """Review item 6: no tag on the bye must not read "back" for a week."""
    team, regular, _ = _team_qbs(data)
    bye = next(w for w in range(2, data.last_week) if data.schedule.opponent(team, w) is None)
    before = [w for w, _ in data.team_by_week[regular] if w < bye][-2:]
    after = next(w for w, _ in data.team_by_week[regular] if w > bye)
    season, team, regular, backup = _season(data, (*before, after), tagged=(*before, after))
    assert _at(season, bye)[backup].state == "out" == _at(season, after)[backup].state


def test_a_franchise_qb_on_preseason_ir_is_the_regular_and_his_week_one_starter_fills_in(data):
    """Review item 7: back from a preseason IR he holds the job; he never took it."""
    team, regular, _ = _team_qbs(data)
    opening = [w for w, _ in data.team_by_week[regular] if w <= 4]
    season, team, regular, backup = _season(data, opening, tagged=())
    season = _with(season, injuries={(regular, w): fakes.Injury("IR") for w in opening})
    assert _at(season, opening[-1])[backup] == replay_fillin.FillIn(team, regular, len(opening) - 1, "out")
    later = [w for w, _ in data.team_by_week[regular] if w > opening[-1]]
    assert _at(season, later[0])[backup].state == "back" and regular not in _at(season, later[1])


def test_a_fill_ins_chart_reads_nothing_from_the_week_on(data):
    """Review item 11: the fill-in's chart at week W is the same whatever happens from W on (later games, tags)."""
    season, team, regular, backup = _season(data, OUT)
    league = rv.league_settings(fakes.fake_settings())
    for week in (5, 6):
        rv.clear_cache()
        now = rv.knowledge(season, league, week, 16, 3).by_id[backup]
        rv.clear_cache()
        changed = rv.knowledge(fakes.future_changed(season, week), league, week, 16, 3).by_id[backup]
        assert now.fill_in is not None and (now.chart, now.fill_in) == (changed.chart, changed.fill_in)
    rv.clear_cache()


def test_a_graduates_chart_keeps_his_form_and_is_his_share_of_his_chart(data, monkeypatch):
    """A graduate (past probation, the job taken) is priced at share x his chart + the rest x a backup's; the chart
    at the week reads nothing from that week on; an established starter is untouched."""
    probation = int(replay_fillin.stored()["probation"])
    weeks = [w for w, _ in data.team_by_week[_team_qbs(data)[1]] if w >= 5]
    season, team, regular, backup = _season(data, weeks, tagged=())
    week, league = weeks[probation], rv.league_settings(fakes.fake_settings())
    rv.clear_cache()
    priced = rv.knowledge(season, league, week, 16, 0).by_id[backup]
    rv.clear_cache()
    changed = rv.knowledge(fakes.future_changed(season, week), league, week, 16, 0).by_id[backup]
    others = {v.player_id: v.chart for v in rv.knowledge(season, league, week, 16, 0).values
              if v.position == "QB" and v.player_id != backup}
    monkeypatch.setattr(replay_fillin, "_fill_ins", lambda data, week, teams: MappingProxyType({}))
    rv.clear_cache()
    know = rv.knowledge(season, league, week, 16, 0)
    plain = know.by_id[backup]
    rv.clear_cache()
    share = replay_fillin.stored()["graduated"]["prior" if season.players[backup].adp is not None
                                                and season.players[backup].adp < replay_fillin.PRIOR_PICK else "none"][0]
    backup_value = replay_chart.value_at(replay_chart.slot_curve(season.as_of(week)), "QB", 33)
    assert priced.fill_in == pytest.approx(share) and priced.form_chart == plain.form_chart
    assert priced.chart == pytest.approx(share * plain.chart + (1 - share) * min(backup_value, plain.chart), abs=0.1)
    assert (changed.chart, changed.fill_in) == (priced.chart, priced.fill_in)
    assert others == {v.player_id: v.chart for v in know.values if v.position == "QB" and v.player_id != backup}


def test_without_news_drops_a_taker_at_probation_as_before(data):
    """The simulator's fill-in set (replay_fillin.without_news) is unchanged by the graduates' climb."""
    probation = int(replay_fillin.stored()["probation"])
    weeks = [w for w, _ in data.team_by_week[_team_qbs(data)[1]] if w >= 5]
    season, team, regular, backup = _season(data, weeks, tagged=())
    assert backup in replay_fillin.without_news(season.as_of(weeks[probation - 1]), weeks[probation - 1])
    assert backup not in replay_fillin.without_news(season.as_of(weeks[probation]), weeks[probation])


@pytest.mark.parametrize("adp", [190.0, 230.0])
def test_a_graduates_prior_group_is_the_same_in_every_lineup_and_league_size(data, adp):
    """`prior` reads the raw 1-QB ADP, the list the fit read: the superflex view (which moves QBs earlier) and the
    league-size taper (which moves an 8-team league's deep QBs later) never move a QB across PRIOR_PICK."""
    probation = int(replay_fillin.stored()["probation"])
    weeks = [w for w, _ in data.team_by_week[_team_qbs(data)[1]] if w >= 5]
    season, team, regular, backup = _season(data, weeks, tagged=())
    season = replace(season, players=MappingProxyType(
        {**season.players, backup: replace(season.players[backup], adp=adp)}))
    week = weeks[probation]
    expected = replay_fillin.stored()["graduated"]["prior" if adp < replay_fillin.PRIOR_PICK else "none"][0]
    superflex = {**fakes.fake_settings().starters, "SUPERFLEX": 1}
    for settings in (fakes.fake_settings(), fakes.fake_settings(starters=superflex),
                     fakes.fake_settings(teams=8), fakes.fake_settings(teams=8, starters=superflex)):
        league = rv.league_settings(settings)
        rv.clear_cache()
        priced = rv.knowledge(season, league, week, 16, 0).by_id[backup]
        with pytest.MonkeyPatch.context() as patch:       # the same week with nobody priced as a fill-in
            patch.setattr(replay_fillin, "_fill_ins", lambda data, week, teams: MappingProxyType({}))
            rv.clear_cache()
            plain = rv.knowledge(season, league, week, 16, 0).by_id[backup]
        curve = rv.chart_view(season.as_of(week), league)[1]           # this league's value curve
        backup_value = replay_chart.value_at(replay_chart.slot_curve(curve), "QB",
                                             len({t for t, _ in season.schedule.opponents}) + 1)
        assert priced.fill_in == pytest.approx(expected), (settings.teams, settings.starters)
        graduate = expected * plain.chart + (1 - expected) * min(backup_value, plain.chart)
        assert priced.chart == pytest.approx(graduate, abs=0.1), (settings.teams, settings.starters)
    rv.clear_cache()
