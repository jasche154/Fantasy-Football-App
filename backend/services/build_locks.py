"""One build at a time per cache key.

Without this, parallel requests that all miss a cache (right after a data load, say) each
run the same expensive build. With it, the first builds and the rest wait, then read the
cache it filled.
"""
from collections.abc import Hashable
from threading import Lock

_locks: dict[Hashable, Lock] = {}
_guard = Lock()
MAX_LOCKS = 256


def key_lock(key: Hashable) -> Lock:
    with _guard:
        lock = _locks.get(key)
        if lock is None:
            if len(_locks) >= MAX_LOCKS:
                idle = next((k for k, held in _locks.items() if not held.locked()), None)
                if idle is not None:                 # never drop a lock someone holds
                    _locks.pop(idle)
            lock = _locks[key] = Lock()
        return lock
