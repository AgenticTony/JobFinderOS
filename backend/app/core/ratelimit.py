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
}


def enforce(user_id, bucket: str) -> None:
    limit, window = BUCKETS[bucket]
    limiter.check((str(user_id), bucket), limit, window)
