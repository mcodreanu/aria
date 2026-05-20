"""Small TTL cache for expensive health/status checks."""

from __future__ import annotations

import time
from typing import Callable, TypeVar

from settings import HEALTH_CACHE_SECONDS

T = TypeVar("T")

_cache: dict[str, tuple[float, object]] = {}


def ttl_cached(key: str, fn: Callable[[], T], ttl: float = HEALTH_CACHE_SECONDS) -> T:
    now = time.time()
    item = _cache.get(key)
    if item and now - item[0] < ttl:
        return item[1]  # type: ignore[return-value]
    value = fn()
    _cache[key] = (now, value)
    return value


def clear_health_cache() -> None:
    _cache.clear()
