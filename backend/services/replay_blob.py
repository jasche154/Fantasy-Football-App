"""Replay League: a simulated league's whole season, frozen into its save (table replay_season_data).

A live or future season is built once when its league is created (services/sim_setup.py) and never rebuilt, so
new real data never changes a saved game. Saves live for years, so the format is versioned, compressed JSON
(never pickle), read by the field types of `SeasonData`: a field or record column missing from an old save
takes its default, an unknown one is ignored. Each mapping is stored as columns ({"k0": [...], "k1": [...],
"v": [...]}; a record value as one list per field), which compresses far better than objects.

`load` returns ONE object per league (SeasonData is hashed by identity and several caches key on it), kept in
a small LRU that replay_pool.clear_cache() empties too; a season dropped from it frees those caches. A league's
season enters the cache only once its row is committed (`remember`), replacing whatever a deleted league with
the same id left there. A save that can't be decoded is an UnreadableSeason (a server-side fault, never bad input).
"""
import json
import logging
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

log = logging.getLogger(__name__)

VERSION = 1                     # the "v" of a stored season (1..VERSION); a newer one is refused, an older one read
# Decoded seasons kept, least recent out. One is ~9 MB decoded and takes ~1.5 s to decode (2026-11, real DB), so
# 12 (~110 MB) lets a user switch between a dozen simulated leagues paying the decode once each per restart.
CACHED = 12
MAX_UNPACKED = 64 * 2**20       # JSON bytes a save may unpack to: far above any season; a tampered row can't balloon
_HINTS = typing.get_type_hints(SeasonData)
_SCHEDULE = {"games": tuple[Game, ...], "opponents": Mapping[tuple[str, int], str]}


class UnreadableSeason(RuntimeError):
    """A league's saved season can't be decoded (truncated, garbled, or saved by a newer app)."""


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
        return kind(**{spec.name: _tuples(value[spec.name]) for spec in fields(kind) if spec.name in value})
    return value


def dumps(data: SeasonData) -> bytes:
    """The season as compressed JSON (exact: loads gives back every field, key and number as it was)."""
    payload = {"v": VERSION, **{spec.name: _encode(_HINTS[spec.name], getattr(data, spec.name))
                                for spec in fields(SeasonData)}}
    return zlib.compress(json.dumps(payload, separators=(",", ":")).encode(), 9)


def _unpack(blob: bytes) -> bytes:
    inflater = zlib.decompressobj()
    raw = inflater.decompress(blob, MAX_UNPACKED)
    if inflater.unconsumed_tail:
        raise UnreadableSeason(f"This league's saved season unpacks to more than {MAX_UNPACKED // 2**20} MB.")
    if not inflater.eof:
        raise zlib.error("incomplete stream")             # what zlib.decompress says of a truncated save
    return raw


def loads(blob: bytes) -> SeasonData:
    """A stored season. UnreadableSeason (the exception's type in its text) when it is not one, or was saved by
    a newer app."""
    try:
        payload = json.loads(_unpack(blob))
        version = payload.get("v")
        if isinstance(version, bool) or not isinstance(version, int) or not 1 <= version <= VERSION:
            shown = version if type(version) is int and abs(version) < 10**9 else type(version).__name__  # tampered
            raise UnreadableSeason(f"This league's season was saved in format version {shown}; this app reads "
                                   f"1 to {VERSION}.")
        return SeasonData(**{spec.name: _decode(_HINTS[spec.name], payload[spec.name])
                             for spec in fields(SeasonData) if spec.name in payload})
    except UnreadableSeason as exc:                     # its text holds a number or a type name, never contents
        log.warning("Unreadable season blob (%s)", exc)
        raise
    except Exception as exc:                            # any decode failure: one clean error, never a 500 trace
        log.warning("Unreadable season blob (%s)", type(exc).__name__)      # the type only: never the contents
        raise UnreadableSeason(f"This league's saved season can't be read ({type(exc).__name__}).") from exc


def freeze(data: SeasonData) -> tuple[bytes, SeasonData]:
    """(the save's bytes, the season as the save holds it: plain floats where the engine made numpy ones), so a
    new league plays the same numbers before and after it is next loaded. One encode, one decode."""
    blob = dumps(data)
    return blob, loads(blob)


# ---------------- store ----------------

_cache: OrderedDict[tuple[str, int], SeasonData] = OrderedDict()
_cache_lock = Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


replay_pool.CLEAR_HOOKS.append(clear_cache)


def _key(session: Session, league_id: int) -> tuple[str, int]:
    return str(session.get_bind().url), league_id


def _keep(key: tuple[str, int], data: SeasonData, replace: bool = False) -> SeasonData:
    """The cached season for `key`: the one already there (a parallel request decoded it first) unless `replace`,
    else `data`."""
    with _cache_lock:
        found = _cache.get(key)
        if found is None or replace:
            found = _cache[key] = data
        _cache.move_to_end(key)
        evicted = len(_cache) > CACHED
        while len(_cache) > CACHED:
            _cache.popitem(last=False)
    if evicted:                                         # other caches would keep the dropped season alive
        replay_pool.free_dependents()
    return found


def store(session: Session, league_id: int, kind: str, known_through: int, blob: bytes) -> None:
    """Add a new league's frozen season (`freeze`'s bytes) to the session; the caller commits it with the league
    row, then `remember`s the decoded season."""
    session.add(ReplaySeasonData(league_id=league_id, kind=kind, known_through=known_through, blob=blob))
    session.flush()


def remember(session: Session, league_id: int, data: SeasonData) -> None:
    """Cache a just-committed league's season as `data` itself, over anything a deleted league with the same id
    (or a request that read it before the delete) left there."""
    _keep(_key(session, league_id), data, replace=True)


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
