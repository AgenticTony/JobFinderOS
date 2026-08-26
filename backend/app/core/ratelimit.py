"""
Per-user rate limiting for AI-spending endpoints.

In-process sliding window (per user+bucket). Adequate for the single-worker
deployment; a multi-worker deploy moves counters to Redis — interface stays.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[tuple, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: tuple, limit: int, window_seconds: float) -> None:
        """Raise 429 when (key) exceeded `limit` calls in the window."""
        now = time.monotonic()
        with self._lock:
            # Evict idle buckets so memory doesn't grow with dead accounts
            if len(self._hits) > 10_000:
                stale = [k for k, v in self._hits.items() if not v or now - v[-1] > window_seconds * 2]
                for k in stale:
                    del self._hits[k]
            bucket = self._hits[key]
            while bucket and now - bucket[0] > window_seconds:
                bucket.popleft()
            if len(bucket) >= limit:
                retry_after = int(window_seconds - (now - bucket[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit reached — try again in {retry_after}s",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)


limiter = SlidingWindowLimiter()

# Buckets: (name, limit, window_s) — sized to protect GLM budget per user
BUCKETS = {
    "cv_upload": (5, 3600),          # 5 CV uploads/hour
    "ai_suggest": (10, 3600),        # query suggestions
    "hunt": (12, 3600),              # manual pipeline runs
    "match_run": (12, 3600),         # matching kicks
    "draft_prepare": (20, 3600),     # tailored packages
    # Auth endpoints — the only routes an attacker can hit without an
    # account. Keyed by the TARGET EMAIL (see app/api/deps.py), not IP:
    # per-account is the meaningful unit for brute force, and every client
    # behind one proxy would otherwise share a bucket. A per-IP layer
    # belongs to the reverse proxy at deployment.
    "auth_register": (5, 3600),      # signup attempts per address
    "auth_login": (10, 900),         # logins per account per 15 min
}


def enforce(user_id, bucket: str) -> None:
    limit, window = BUCKETS[bucket]
    limiter.check((str(user_id), bucket), limit, window)


def clear_user(user_id) -> None:
    """GDPR: purge a deleted account's in-memory rate-limit entries."""
    with limiter._lock:
        for key in [k for k in limiter._hits if k[0] == str(user_id)]:
            del limiter._hits[key]
