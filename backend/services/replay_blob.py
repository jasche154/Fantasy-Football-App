"""Replay League: a simulated league's whole season, frozen into its save (table replay_season_data).

A live or future season is built once when its league is created (services/sim_setup.py) and never rebuilt, so
new real data never changes a saved game. Saves live for years, so the format is versioned, compressed JSON
(never pickle), read by the field types of `SeasonData`: a field or record column missing from an old save
takes its default, an unknown one is ignored. Each mapping is stored as columns ({"k0": [...], "k1": [...],
"v": [...]}; a record value as one list per field), which compresses far better than objects.

`load` returns ONE object per league (SeasonData is hashed by identity and several caches key on it), kept in
a small LRU that replay_pool.clear_cache() empties too; a season dropped from it frees those caches.
"""
import json
import types
import typing
import zlib
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from threading import Lock
from types import MappingProxyType

import numpy as np
from sqlalchemy.orm import Session

from database.models import ReplaySeasonData
from services import replay_pool
from services.replay_pool import SeasonData
from services.schedule_strength import Game, Schedule
from services.valuation import NoDataError

VERSION = 1                     # the "v" of a stored season; a newer one is refused, an older one read
CACHED = 4                      # decoded seasons kept, least recent out
_HINTS = typing.get_type_hints(SeasonData)
_SCHEDULE = {"games": tuple[Game, ...], "opponents": Mapping[tuple[str, int], str]}


# ---------------- codec ----------------

def _plain(value):
    """A JSON value: numpy scalars as Python ones, tuples as lists."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _tuples(value):
    return tuple(_tuples(item) for item in value) if isinstance(value, list) else value


def _columns(records, kind: type) -> dict[str, list]:
    return {spec.name: [_plain(getattr(record, spec.name)) for record in records] for spec in fields(kind)}


def _records(columns: Mapping[str, list], kind: type, count: int) -> list:
    known = [spec.name for spec in fields(kind) if spec.name in columns]    # a missing column keeps its default
    return [kind(**{name: _tuples(columns[name][i]) for name in known}) for i in range(count)]


def _encode(kind, value):
    if value is None:
        return None
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    if origin in (typing.Union, types.UnionType):
        return _encode(next(arg for arg in args if arg is not type(None)), value)
    if kind is Schedule:
        return {name: _encode(sub, getattr(value, name)) for name, sub in _SCHEDULE.items()}
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis and is_dataclass(args[0]):
        return _columns(value, args[0])
    if origin is not None and issubclass(origin, Mapping):
        key_kind, item_kind = args
        keys = list(value)
        out = ({f"k{i}": [_plain(key[i]) for key in keys] for i in range(len(typing.get_args(key_kind)))}
               if typing.get_origin(key_kind) is tuple else {"k": [_plain(key) for key in keys]})
        items = [value[key] for key in keys]
        out["v"] = _columns(items, item_kind) if is_dataclass(item_kind) else [_plain(item) for item in items]
        return out
    if is_dataclass(kind):
        return {spec.name: _plain(getattr(value, spec.name)) for spec in fields(kind)}
    return _plain(value)


def _decode(kind, value):
    if value is None:
        return None
    origin, args = typing.get_origin(kind), typing.get_args(kind)
    if origin in (typing.Union, types.UnionType):
        return _decode(next(arg for arg in args if arg is not type(None)), value)
    if kind is Schedule:
        return Schedule(**{name: _decode(sub, value[name]) for name, sub in _SCHEDULE.items()})
    if origin is tuple and len(args) == 2 and args[1] is Ellipsis and is_dataclass(args[0]):
        return tuple(_records(value, args[0], len(next(iter(value.values()), []))))
    if origin is not None and issubclass(origin, Mapping):
        key_kind, item_kind = args
        if "k" in value:
            keys = value["k"]
        else:
            keys = list(zip(*(value[f"k{i}"] for i in range(len(typing.get_args(key_kind))))))
        items = (_records(value["v"], item_kind, len(keys)) if is_dataclass(item_kind)
                 else [_tuples(item) for item in value["v"]])
        return MappingProxyType(dict(zip(keys, items, strict=True)))
    if is_dataclass(kind):
        return kind(**{spec.name: value[spec.name] for spec in fields(kind) if spec.name in value})
    return value


def dumps(data: SeasonData) -> bytes:
    """The season as compressed JSON (exact: loads gives back every field, key and number as it was)."""
    payload = {"v": VERSION, **{spec.name: _encode(_HINTS[spec.name], getattr(data, spec.name))
                                for spec in fields(SeasonData)}}
    return zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)


def loads(blob: bytes) -> SeasonData:
    """A stored season. ValueError when it is not one, or was saved by a newer app."""
    try:
        payload = json.loads(zlib.decompress(blob))
        version = payload.get("v")
    except (zlib.error, json.JSONDecodeError, UnicodeDecodeError, AttributeError) as exc:
        raise ValueError(f"Not a stored replay season: {type(exc).__name__}") from exc
    if not isinstance(version, int) or version > VERSION:
        raise ValueError(f"This league's season was saved in format version {version}; this app reads {VERSION}.")
    try:
        return SeasonData(**{spec.name: _decode(_HINTS[spec.name], payload[spec.name])
                             for spec in fields(SeasonData) if spec.name in payload})
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise ValueError(f"Not a stored replay season: {type(exc).__name__}") from exc


# ---------------- store ----------------

_cache: OrderedDict[tuple[str, int], SeasonData] = OrderedDict()
_cache_lock = Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


replay_pool.CLEAR_HOOKS.append(clear_cache)


def _key(session: Session, league_id: int) -> tuple[str, int]:
    return str(session.get_bind().url), league_id


def _keep(key: tuple[str, int], data: SeasonData) -> SeasonData:
    """The cached season for `key`: the one already there (a parallel request decoded it first), else `data`."""
    with _cache_lock:
        found = _cache.get(key)
        if found is None:
            found = _cache[key] = data
        _cache.move_to_end(key)
        evicted = len(_cache) > CACHED
        while len(_cache) > CACHED:
            _cache.popitem(last=False)
    if evicted:                                         # other caches would keep the dropped season alive
        replay_pool.free_dependents()
    return found


def store(session: Session, league_id: int, kind: str, known_through: int, data: SeasonData) -> SeasonData:
    """Add a new league's frozen season to the session (the caller commits it with the league row) and cache
    `data` itself as that league's season; returns it."""
    session.add(ReplaySeasonData(league_id=league_id, kind=kind, known_through=known_through, blob=dumps(data)))
    session.flush()
    with _cache_lock:
        _cache.pop(_key(session, league_id), None)      # a deleted league's id may be reused
    return _keep(_key(session, league_id), data)


def load(session: Session, league_id: int) -> SeasonData:
    """A league's frozen season, the same object on every call. NoDataError when it has none."""
    key = _key(session, league_id)
    with _cache_lock:
        found = _cache.get(key)
        if found is not None:
            _cache.move_to_end(key)
            return found
    row = session.get(ReplaySeasonData, league_id)
    if row is None:
        raise NoDataError(f"Replay league {league_id} has no stored season.")
    return _keep(key, loads(row.blob))


def forget(session: Session, league_id: int) -> None:
    """Drop a deleted league's season from the cache (its id may be given to the next league)."""
    with _cache_lock:
        _cache.pop(_key(session, league_id), None)
