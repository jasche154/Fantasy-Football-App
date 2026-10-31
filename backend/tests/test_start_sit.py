"""Start/sit decision tool: confidence, locks, simulated head-to-head odds, league scoring, size."""
import json

import pytest

from database.models import Player
from services import game_locks
from services import my_team as mt
from services import start_sit
from services import weekly_rankings as weekly
from services.league import LeagueSettings
from tests.test_injury_model import _seed_week
from tests.test_my_team import MINE, THEIRS, _seed_league
from tools.registry import ToolError, ToolRegistry
from tools.start_sit_tool import START_SIT

TOOLS = ToolRegistry([START_SIT])
# _seed_week gives these four an ESPN projection; with outside sources loaded, only they get a weekly row.
FOUR = ["RB Player 01", "RB Player 02", "WR Player 01", "WR Player 40"]


def _ids(session) -> dict[str, int]:
    return {p.full_name: p.id for p in session.query(Player).all()}


@pytest.fixture
def week(session, seeded):
    _seed_week(session)
    return _ids(session)


@pytest.fixture
def league(session, week):
    return _seed_league(session, week, MINE + [("WR Player 40", "BN")], THEIRS)


def _call(session, **arguments) -> dict:
    return TOOLS.call(session, "start_sit", arguments)


@pytest.mark.parametrize("gap, expected", [(0.0, "toss_up"), (1.49, "toss_up"), (1.5, "lean"),
                                           (2.99, "lean"), (3.0, "clear"), (12.0, "clear")])
def test_confidence_uses_the_lineup_cutoffs(gap, expected):
    assert start_sit.confidence_for(gap) == expected


def test_works_without_a_yahoo_league(session, week):
    result = _call(session, players=["WR Player 01", "WR Player 40"])
    assert result["scoring"] == "ppr" and "league" not in result
    assert result["decision"]["start"] == "WR Player 01 (WR, AAA)"
    assert result["decision"]["confidence"] in ("toss_up", "lean", "clear")
    assert "WR Player 01" in result["decision"]["verdict"]
    assert {row["player"].split(" (")[0] for row in result["players"]} == {"WR Player 01", "WR Player 40"}


def test_p_outscores_is_a_symmetric_probability(session, week):
    by_id = {p.player_id: p for p in weekly.build_weekly_rankings(session, LeagueSettings()).players}
    a, b = by_id[week["RB Player 01"]], by_id[week["WR Player 40"]]
    odds = start_sit.outscore_probabilities([a, b])
    assert all(0.0 <= v <= 1.0 for v in odds.values())
    assert odds[(a.player_id, b.player_id)] + odds[(b.player_id, a.player_id)] == pytest.approx(1.0, abs=1e-9)
    assert odds == start_sit.outscore_probabilities([a, b])              # fixed seed: deterministic
    result = _call(session, players=["WR Player 01", "WR Player 40"])
    assert 0.5 <= result["decision"]["p_outscores"] <= 1.0               # the pick is favored


def test_locked_player_is_never_the_pick(session, week, league, monkeypatch):
    star = week["WR Player 01"]
    monkeypatch.setattr(game_locks, "locked_ids", lambda players, kickoff, now=None: {star})
    weekly._cache.clear()
    result = _call(session, players=["WR Player 01", "WR Player 40"])
    assert result["decision"]["start"].startswith("WR Player 40")
    locked_row = next(r for r in result["players"] if r["player"].startswith("WR Player 01"))
    assert locked_row["locked"] is True
    assert any("kicked off" in note for note in result["notes"])


def test_all_locked_means_no_pick(session, week, league, monkeypatch):
    ids = {week["WR Player 01"], week["WR Player 40"]}
    monkeypatch.setattr(game_locks, "locked_ids", lambda players, kickoff, now=None: ids)
    result = _call(session, players=["WR Player 01", "WR Player 40"])
    assert "start" not in result["decision"] and "kicked off" in result["decision"]["verdict"]


def test_uses_the_league_scoring(session, week, league):
    league.scoring_settings = json.dumps({**json.loads(league.scoring_settings), "scoring": "half"})
    session.commit()
    weekly._cache.clear()
    result = _call(session, players=["WR Player 01", "WR Player 40"])
    assert result["scoring"] == "half" and result["league"] == "Family League"
    expected = {p.player_id: p.projection
                for p in weekly.build_weekly_rankings(session, mt.league_settings(league)).players}
    row = next(r for r in result["players"] if r["player"].startswith("WR Player 01"))
    assert row["proj"] == pytest.approx(expected[week["WR Player 01"]], abs=0.05)
    ppr = {p.player_id: p.projection for p in weekly.build_weekly_rankings(session, LeagueSettings()).players}
    assert row["proj"] < ppr[week["WR Player 01"]]                     # half PPR scores receptions less


def test_picks_the_league_that_rosters_the_players(session, week):
    _seed_league(session, week, MINE, THEIRS, name="Alpha")
    _seed_league(session, week, [("WR Player 40", "WR"), ("RB Player 02", "BN")], THEIRS, name="Zeta")
    result = _call(session, players=["WR Player 40", "RB Player 02"])
    assert result["league"] == "Zeta"
    assert _call(session, players=["WR Player 40", "RB Player 02"], league="alpha")["league"] == "Alpha"


def test_four_players_fit_the_size_budget(session, league):
    result = _call(session, players=FOUR)
    assert len(result["players"]) == 4
    assert len(json.dumps(result)) <= 3000


def test_slot_filters_ineligible_players(session, week):
    result = _call(session, players=["RB Player 01", "WR Player 01", "WR Player 40"], slot="WR")
    assert result["decision"]["start"].startswith("WR Player 01")
    assert any("RB Player 01 can't play WR" in note for note in result["notes"])


def test_player_without_a_projection_is_noted_not_an_error(session, week):
    result = _call(session, players=["WR Player 01", "TE Player 01"])
    assert result["decision"]["start"].startswith("WR Player 01")
    assert any(note.startswith("TE Player 01: no projection") for note in result["notes"])


@pytest.mark.parametrize("arguments, message", [
    ({"players": ["WR Player 01", "wr player 01"]}, "two different players"),
    ({"players": ["WR Player 01", "WR Player 40"], "league": "nope"}, "No Yahoo league"),
])
def test_bad_requests_raise_tool_errors(session, week, arguments, message):
    with pytest.raises(ToolError, match=message):
        _call(session, **arguments)
