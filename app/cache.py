"""In-process TTL cache with explicit keys, statistics and manual invalidation.

Earth Engine products are expensive: a basin composite plus zonal statistics can
take several seconds.  Every cache key encodes *all* inputs that affect the
result (basin, indicator, window, scale, mask version, processing version) so a
stale product can never be returned for a different request.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Optional

from .logging_config import get_logger

logger = get_logger(__name__)


class TTLCache:
    """Small thread-safe TTL cache with hit/miss accounting."""

    def __init__(self, maxsize: int = 256, ttl_seconds: int = 3600, name: str = "cache") -> None:
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.Lock()
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self.name = name
        self.hits = 0
        self.misses = 0

    # ------------------------------------------------------------------ basic
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                self.misses += 1
                return None
            expires_at, value = entry
            if time.time() > expires_at:
                del self._data[key]
                self.misses += 1
                return None
            self.hits += 1
            return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        with self._lock:
            if len(self._data) >= self.maxsize:
                self._evict_locked()
            self._data[key] = (time.time() + ttl, value)

    def has(self, key: str) -> bool:
        return self.get(key) is not None

    # ------------------------------------------------------------- management
    def clear(self, prefix: Optional[str] = None) -> int:
        with self._lock:
            if prefix is None:
                removed = len(self._data)
                self._data.clear()
                return removed
            keys = [key for key in self._data if key.startswith(prefix)]
            for key in keys:
                del self._data[key]
            return len(keys)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            return {
                "name": self.name,
                "size": len(self._data),
                "maxsize": self.maxsize,
                "ttl_seconds": self.ttl_seconds,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": round(self.hits / total, 3) if total else None,
            }

    def _evict_locked(self) -> None:
        now = time.time()
        expired = [key for key, (expires_at, _) in self._data.items() if now > expires_at]
        for key in expired:
            del self._data[key]
        if len(self._data) >= self.maxsize and self._data:
            oldest = min(self._data.items(), key=lambda item: item[1][0])[0]
            del self._data[oldest]


def cache_key(*parts: Any, version: str = "1") -> str:
    """Build a deterministic cache key from all inputs that affect a product."""

    joined = "|".join(str(part) for part in parts)
    return f"v{version}:{joined}"
