"""In-process TTL cache for expensive runtime fetches (sectors, market, etc.)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

# key -> (expires_at_monotonic, value)
_store: dict[str, tuple[float, Any]] = {}


def get_or_set(key: str, ttl_sec: float, factory: Callable[[], T]) -> T:
    now = time.monotonic()
    hit = _store.get(key)
    if hit is not None and now < hit[0]:
        return hit[1]  # type: ignore[return-value]
    val = factory()
    _store[key] = (now + max(0.0, float(ttl_sec)), val)
    return val


def set_value(key: str, value: Any, ttl_sec: float) -> None:
    _store[key] = (time.monotonic() + max(0.0, float(ttl_sec)), value)


def get_value(key: str) -> Any | None:
    now = time.monotonic()
    hit = _store.get(key)
    if hit is None or now >= hit[0]:
        if hit is not None:
            _store.pop(key, None)
        return None
    return hit[1]


def invalidate(key: str) -> None:
    _store.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    dead = [k for k in _store if k.startswith(prefix)]
    for k in dead:
        _store.pop(k, None)


def purge_expired() -> int:
    now = time.monotonic()
    dead = [k for k, (exp, _) in _store.items() if now >= exp]
    for k in dead:
        _store.pop(k, None)
    return len(dead)


def cache_stats() -> dict[str, int]:
    purge_expired()
    return {"entries": len(_store)}
