"""sim_offseason: the 2027 offseason file loads and validates (no database)."""
import json

import pytest

from services import sim_offseason as so
from services.replay_pool import PoolPlayer
from services.sim_schedule import TEAMS


def _raw() -> dict:
    return json.loads(so.path(2027).read_text(encoding="utf-8"))


def test_the_2027_file_loads_with_the_users_pick():
    rookies, moves = so.load(2027)
    assert 45 <= len(rookies) <= 60 and 10 <= len(moves) <= 30
    smith = next(r for r in rookies if r.name == "Jeremiah Smith")
    assert (smith.team, smith.position, smith.pick) == ("MIA", "WR", 1)
    assert all(r.team in TEAMS and 1 <= r.pick <= so.MAX_PICK for r in rookies)
    assert any(m.team is None for m in moves) and all(m.note for m in moves)
    raw = _raw()
    assert raw["as_of"] == "2026-11-23" and raw["sources"]


def test_missing_season_has_nothing():
    assert so.load(1999) == ((), ())


@pytest.mark.parametrize("change, message", [
    (lambda raw: raw["rookies"][0].update(team="XXX"), "unknown team code 'XXX'"),
    (lambda raw: raw["rookies"][1].update(position="LB"), "position 'LB'"),
    (lambda raw: raw["rookies"][2].update(pick=300), "pick 300"),
    (lambda raw: raw["rookies"][3].update(pick=raw["rookies"][4]["pick"]), "used twice"),
    (lambda raw: raw["moves"].append({**raw["moves"][0]}), "listed twice"),
    (lambda raw: raw["moves"].append({"name": "Jeremiah Smith Jr.", "position": "WR", "team": None, "note": "x"}),
     "listed twice"),
    (lambda raw: raw["moves"][0].update(team="OAK"), "unknown team code 'OAK'"),
    (lambda raw: raw["moves"][0].update(note=""), "'note'"),
])
def test_bad_entries_name_the_problem(change, message):
    raw = _raw()
    change(raw)
    with pytest.raises(ValueError, match=message):
        so.parse(raw)


def test_match_normalises_names_and_reports_the_rest():
    players = {1: PoolPlayer(1, "Odell Beckham", "WR"), 2: PoolPlayer(2, "A.J. Brown", "WR"),
               3: PoolPlayer(3, "Mike Williams", "WR"), 4: PoolPlayer(4, "Mike Williams", "WR"),
               5: PoolPlayer(5, "Josh Allen", "QB")}
    moves = [so.Move("Odell Beckham Jr.", "WR", None, "x"), so.Move("AJ Brown", "WR", "NE", "x"),
             so.Move("Mike Williams", "WR", None, "x"), so.Move("Josh Allen", "RB", None, "x"),
             so.Move("Nobody Here", "TE", None, "x")]
    found, missing = so.match(moves, players)
    assert found == {"Odell Beckham Jr.": 1, "AJ Brown": 2}
    assert missing == ("Mike Williams", "Josh Allen", "Nobody Here")


@pytest.mark.parametrize("raw, message", [
    ([], "must be a JSON object"),
    ("rookies", "must be a JSON object"),
    ({"rookies": 5}, "'rookies' must be a list"),
    ({"rookies": "Jeremiah Smith"}, "'rookies' must be a list"),
    ({"moves": {"name": "x"}}, "'moves' must be a list"),
    ({"rookies": [5]}, r"rookies\[0\]: each entry must be an object"),
    ({"moves": [["Aaron Rodgers"]]}, r"moves\[0\]: each entry must be an object"),
])
def test_a_badly_shaped_file_is_a_value_error_naming_the_problem(raw, message):
    with pytest.raises(ValueError, match=message):
        so.parse(raw)


def test_a_badly_shaped_file_on_disk_is_a_value_error(monkeypatch, tmp_path):
    monkeypatch.setattr(so, "DATA_DIR", tmp_path)
    so.path(2030).write_text('{"rookies": 5}', encoding="utf-8")
    with pytest.raises(ValueError, match="'rookies' must be a list"):
        so.load(2030)
