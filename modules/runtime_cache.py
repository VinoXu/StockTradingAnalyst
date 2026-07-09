"""In-process TTL cache for expensive runtime fetches (sectors, market, etc.)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")

_store: dict[str, tuple[float, Any]] = {}


def get_or_set(key: str, ttl_sec: float, factory: Callable[[], T]) -> T:
    now = time.monotonic()
    hit = _store.get(key)
    if hit is not None and now - hit[0] < ttl_sec:
        return hit[1]  # type: ignore[return-value]
    val = factory()
    _store[key] = (now, val)
    return val


def invalidate(key: str) -> None:
    _store.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    dead = [k for k in _store if k.startswith(prefix)]
    for k in dead:
        _store.pop(k, None)


def cache_stats() -> dict[str, int]:
    return {"entries": len(_store)}
