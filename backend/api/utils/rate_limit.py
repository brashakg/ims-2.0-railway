"""THE per-key sliding-window rate limit (in memory, per process).

ponytail: per-process memory -- each worker counts on its own and a restart
resets the counts; move to Redis if a limit must hold across workers.
"""

import time
from typing import Dict, List, Optional


def allow(bucket: Dict[str, List[float]], key: str, limit: int, window_s: float,
          now: Optional[float] = None) -> bool:
    """True (and the hit is recorded) while ``key`` has made fewer than
    ``limit`` hits in the last ``window_s`` seconds; False once it is at the
    limit (the refused hit is not recorded)."""
    now = time.time() if now is None else now
    hits = [t for t in bucket.get(key, ()) if t > now - window_s]
    if len(hits) >= limit:
        bucket[key] = hits
        return False
    hits.append(now)
    bucket[key] = hits
    return True
