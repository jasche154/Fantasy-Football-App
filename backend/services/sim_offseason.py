"""Simulated seasons: the offseason before a simulated season, from a hand-editable file.

backend/sim_data/offseason_<season>.json holds the incoming rookies (name, position, mock landing team, projected
overall pick, college, optional `lock`; from current mock drafts: each league's seeded draft places the unlocked
ones, services/sim_rookie_draft.py) and a short list of moves (retirements: team null, and big
free agents with their most reported destination). Everyone else stays on his current team. `load` validates
the file and raises ValueError naming the bad entry; a season with no file has no rookies and no moves.
`match` finds each move's player in a pool by the name key the replay imports already match on
(integrations.adp_history.name_key: lowercase, no punctuation or Jr./III, known aliases) plus position.
"""
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from integrations.adp_history import name_key
from services.replay_pool import PoolPlayer
from services.sim_schedule import TEAMS

DATA_DIR = Path(__file__).resolve().parents[1] / "sim_data"
POSITIONS = ("QB", "RB", "WR", "TE", "K")
MAX_PICK = 262                          # 7 rounds of 32 plus the most compensatory picks a draft has had


@dataclass(frozen=True)
class Rookie:
    name: str
    position: str
    team: str                           # schedule team code
    pick: int                           # projected overall pick (the prospect's rank in the seeded draft)
    college: str
    lock: bool = False                  # True: he goes to `team` at `pick` in every league (sim_rookie_draft)


@dataclass(frozen=True)
class Move:
    name: str
    position: str
    team: str | None                    # None = retired / out of the league
    note: str


def path(season: int) -> Path:
    return DATA_DIR / f"offseason_{season}.json"


def _text(row: Mapping, key: str, where: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: '{key}' must be a non-empty string.")
    return value.strip()


def _common(row: Mapping, where: str) -> tuple[str, str]:
    if not isinstance(row, Mapping):
        raise ValueError(f"{where}: each entry must be an object.")
    name, position = _text(row, "name", where), _text(row, "position", where)
    if position not in POSITIONS:
        raise ValueError(f"{where} ({name}): position {position!r} is not one of {', '.join(POSITIONS)}.")
    return name, position


def _team(value, where: str) -> str:
    if value not in TEAMS:
        raise ValueError(f"{where}: unknown team code {value!r}.")
    return value


def _rookie(row: Mapping, where: str) -> Rookie:
    name, position = _common(row, where)
    pick = row.get("pick")
    if not isinstance(pick, int) or isinstance(pick, bool) or not 1 <= pick <= MAX_PICK:
        raise ValueError(f"{where} ({name}): pick {pick!r} must be a whole number from 1 to {MAX_PICK}.")
    lock = row.get("lock", False)
    if not isinstance(lock, bool):
        raise ValueError(f"{where} ({name}): lock {lock!r} must be true or false.")
    return Rookie(name, position, _team(row.get("team"), f"{where} ({name})"), pick, _text(row, "college", where),
                  lock=lock)


def _move(row: Mapping, where: str) -> Move:
    name, position = _common(row, where)
    team = row.get("team")
    return Move(name, position, None if team is None else _team(team, f"{where} ({name})"), _text(row, "note", where))


def _entries(raw: Mapping, key: str) -> list:
    value = raw.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"'{key}' must be a list of objects.")
    return value


def parse(raw: Mapping) -> tuple[tuple[Rookie, ...], tuple[Move, ...]]:
    """Validated rookies and moves from the file's JSON object. Any wrong shape is a ValueError naming it."""
    if not isinstance(raw, Mapping):
        raise ValueError("the file must be a JSON object with 'rookies' and 'moves' lists.")
    rookies = tuple(_rookie(row, f"rookies[{i}]") for i, row in enumerate(_entries(raw, "rookies")))
    moves = tuple(_move(row, f"moves[{i}]") for i, row in enumerate(_entries(raw, "moves")))
    seen: dict[str, str] = {}
    for kind, entries in (("rookies", rookies), ("moves", moves)):
        for entry in entries:
            key = name_key(entry.name)
            if key in seen:
                raise ValueError(f"{kind}: {entry.name} is listed twice (also as {seen[key]}).")
            seen[key] = entry.name
    picks = [r.pick for r in rookies]
    taken = {p for p in picks if picks.count(p) > 1}
    if taken:
        raise ValueError(f"rookies: pick {min(taken)} is used twice.")
    return rookies, moves


def load(season: int) -> tuple[tuple[Rookie, ...], tuple[Move, ...]]:
    """The season's rookies and moves; ((), ()) when it has no file. Raises ValueError on a bad entry."""
    file = path(season)
    if not file.exists():
        return (), ()
    try:
        raw = json.loads(file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{file.name} is not valid JSON (line {exc.lineno}).") from exc
    return parse(raw)


def match(moves: Iterable[Move], players: Mapping[int, PoolPlayer]) -> tuple[dict[str, int], tuple[str, ...]]:
    """({move name: player id}, unmatched names). A name two pool players share at that position is unmatched."""
    index: dict[tuple[str, str], list[int]] = {}
    for pid, player in players.items():
        index.setdefault((name_key(player.name), player.position), []).append(pid)
    found, missing = {}, []
    for move in moves:
        ids = index.get((name_key(move.name), move.position), [])
        if len(ids) == 1:
            found[move.name] = ids[0]
        else:
            missing.append(move.name)
    return found, tuple(missing)
