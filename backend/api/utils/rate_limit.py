"""THE per-key sliding-window rate limit.

The window lives in the shared cache seam (api/services/cache.py): Redis when
configured, so every worker counts against the same window and a restart
does not reset it; the in-memory fallback otherwise. Fail-open: a broken
cache never blocks a request.
"""

import math
import time
from typing import Optional


def allow(key: str, limit: int, window_s: float, now: Optional[float] = None) -> bool:
    """True (and the hit is recorded) while ``key`` has made fewer than
    ``limit`` hits in the last ``window_s`` seconds; False once it is at the
    limit (the refused hit is not recorded). Callers name their own key
    (``"webhook:razorpay:1.2.3.4"``); it is stored as ``rl:<key>`` with a TTL
    of one window, so idle buckets expire on their own."""
    try:
        from api.services.cache import cache

        now = time.time() if now is None else now
        cache_key = f"rl:{key}"
        stamps = cache.get(cache_key)
        if not isinstance(stamps, list):
            stamps = []
        stamps = [t for t in stamps if isinstance(t, (int, float)) and t > now - window_s]
        if len(stamps) >= limit:
            return False
        stamps.append(now)
        cache.set(cache_key, stamps, ttl=max(1, math.ceil(window_s)))
        return True
    except Exception:  # noqa: BLE001 - fail-open
        return True
