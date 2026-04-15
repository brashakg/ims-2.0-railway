"""
IMS 2.0 - Caching Layer
========================
Redis-first cache with automatic in-memory TTL fallback.
If Redis is not configured or unreachable, uses a lightweight
in-memory dict with expiry — no dependency required.

Usage:
    from api.services.cache import cache

    # Cache a value
    cache.set("products:BV-BOK-01", product_list, ttl=300)

    # Read with fallback
    data = cache.get("products:BV-BOK-01")
    if data is None:
        data = db_query()
        cache.set("products:BV-BOK-01", data, ttl=300)

    # Invalidate
    cache.delete("products:BV-BOK-01")
    cache.delete_pattern("products:*")  # Redis only; no-op in memory mode
"""

import json
import time
import os
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ── Redis client (optional) ────────────────────────────────────────────
_redis_client = None
try:
    import redis as _redis_lib

    _redis_url = os.getenv("REDIS_URL") or None
    _redis_host = os.getenv("REDIS_HOST")
    _redis_port = int(os.getenv("REDIS_PORT", "6379"))
    _redis_password = os.getenv("REDIS_PASSWORD") or None
    _redis_db = int(os.getenv("REDIS_DB", "0"))

    if _redis_url:
        _redis_client = _redis_lib.from_url(_redis_url, decode_responses=True)
    elif _redis_host:
        _redis_client = _redis_lib.Redis(
            host=_redis_host,
            port=_redis_port,
            password=_redis_password,
            db=_redis_db,
            decode_responses=True,
            socket_connect_timeout=2,
        )

    if _redis_client:
        _redis_client.ping()
        logger.info("[CACHE] Redis connected")
except Exception as e:
    _redis_client = None
    logger.info(f"[CACHE] Redis not available ({e}) — using in-memory cache")


# ── In-memory fallback ─────────────────────────────────────────────────
class _MemoryStore:
    """Simple TTL dict cache. Thread-safe enough for single-process FastAPI."""

    def __init__(self, max_keys: int = 2000):
        self._store: dict = {}
        self._max_keys = max_keys

    def get(self, key: str) -> Optional[str]:
        entry = self._store.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if time.time() > expires_at:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: str, ttl: int = 300):
        # Evict oldest if at capacity
        if len(self._store) >= self._max_keys:
            oldest_key = min(self._store, key=lambda k: self._store[k][1])
            self._store.pop(oldest_key, None)
        self._store[key] = (value, time.time() + ttl)

    def delete(self, key: str):
        self._store.pop(key, None)

    def flush(self):
        self._store.clear()


_memory = _MemoryStore()


# ── Public API ──────────────────────────────────────────────────────────
class CacheService:
    """Unified cache interface — Redis if available, else in-memory."""

    # Default TTLs by category (seconds)
    TTL_SHORT = 60        # 1 min — frequently changing data
    TTL_MEDIUM = 300      # 5 min — product listings, inventory counts
    TTL_LONG = 900        # 15 min — store settings, feature toggles
    TTL_STATIC = 3600     # 1 hour — rarely changing reference data

    @property
    def backend(self) -> str:
        return "redis" if _redis_client else "memory"

    def get(self, key: str) -> Optional[Any]:
        """Get a cached value. Returns None on miss."""
        try:
            if _redis_client:
                raw = _redis_client.get(f"ims:{key}")
            else:
                raw = _memory.get(key)

            if raw is None:
                return None
            return json.loads(raw)
        except Exception:
            return None

    def set(self, key: str, value: Any, ttl: int = TTL_MEDIUM):
        """Cache a JSON-serializable value with TTL in seconds."""
        try:
            raw = json.dumps(value, default=str)
            if _redis_client:
                _redis_client.setex(f"ims:{key}", ttl, raw)
            else:
                _memory.set(key, raw, ttl)
        except Exception as e:
            logger.debug(f"Cache set failed for {key}: {e}")

    def delete(self, key: str):
        """Delete a specific key."""
        try:
            if _redis_client:
                _redis_client.delete(f"ims:{key}")
            else:
                _memory.delete(key)
        except Exception:
            pass

    def delete_pattern(self, pattern: str):
        """Delete keys matching a glob pattern (Redis only; no-op in memory)."""
        try:
            if _redis_client:
                cursor = 0
                while True:
                    cursor, keys = _redis_client.scan(cursor, match=f"ims:{pattern}", count=100)
                    if keys:
                        _redis_client.delete(*keys)
                    if cursor == 0:
                        break
        except Exception:
            pass

    def invalidate_store(self, store_id: str):
        """Convenience: clear all cached data for a store."""
        self.delete_pattern(f"*:{store_id}:*")
        self.delete_pattern(f"*:{store_id}")

    def flush(self):
        """Clear entire cache."""
        try:
            if _redis_client:
                # Only flush ims: prefixed keys
                self.delete_pattern("*")
            else:
                _memory.flush()
        except Exception:
            pass


# Singleton
cache = CacheService()
