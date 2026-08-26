"""
UTC clock helpers.

utc_now() is deprecated (removal scheduled upstream). Replacement
must stay NAIVE-UTC: the schema stores naive datetimes, and mixing aware
and naive values raises on comparison. utc_now() keeps the existing
storage semantics while removing the deprecated call.
"""

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Naive UTC 'now' — drop-in replacement for datetime.utcnow()."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
