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
    # MIG-WO2: the four auth buckets (register/login, per-email and
    # per-IP) are deleted — signup and login moved to Supabase Auth,
    # which enforces its own rate limits. These per-USER buckets are the
    # ceiling on GLM/Resend spend and stay keyed by the mirror-row id.
    # P1-3 (beta review): the send/spam chain had NO throttle — job
    # create (caller-controlled application_email), draft update, submit
    # and retry were all unlimited (live: 25 jobs in one burst, all 201).
    # Sized like the AI-budget buckets above: a legitimate power user
    # never touches them, a scripted burst stops at the boundary.
    "job_create": (20, 3600),        # manual postings per hour
    "draft_update": (60, 3600),      # package edits per hour
    "draft_submit": (20, 3600),      # submissions per hour
    "application_retry": (20, 3600), # failed-send retries per hour
    # The hard ceiling on ACTUAL employer emails: hourly buckets alone
    # still allow a patient (or scripted) account to reach hundreds of
    # employers a day from the shared APPLY_FROM_EMAIL domain — a
    # deliverability and spam vector for every other user's applications.
    "send_daily": (50, 86400),       # employer emails per account per day
    # Beta feedback page — the ceiling exists so a stuck submit button
    # (or a script) can't flood the owner's notification inbox.
    "feedback": (5, 3600),           # feedback submissions per hour
}


def enforce(user_id, bucket: str) -> None:
    limit, window = BUCKETS[bucket]
    limiter.check((str(user_id), bucket), limit, window)


def clear_user(user_id) -> None:
    """GDPR: purge a deleted account's in-memory rate-limit entries."""
    with limiter._lock:
        for key in [k for k in limiter._hits if k[0] == str(user_id)]:
            del limiter._hits[key]


# MIG-WO2: clear_email (the GDPR purge for the email-keyed auth buckets)
# is deleted with those buckets — clear_user above remains and covers
# every surviving per-user key.
