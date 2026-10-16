"""WO-31: the durable AI job queue.

Every user-waiting AI action (tailor, recheck, extract, suggest) is a
row here, drained by ONE loop (app/services/ai_queue.py). Provider load
re-queues a row with next_attempt_at; it never fails it — 'failed' is
for non-load causes only, each carrying a user-facing `error`.

Idempotency: a partial UNIQUE on (kind, user_id, subject_id) while the
row is queued/running — pressing Prepare twice can never create a second
job (or a second draft). Exactly-once result: the drainer's result
transaction flips status with a compare-and-set on lease_token, so a
drainer that lost its lease drops its result instead of double-writing.
"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)

from app.core.orm import Base
from app.core.timeutil import utc_now

# Partial-unique predicate: one ACTIVE job per (kind, user, subject).
ACTIVE_STATUSES = ("queued", "running")
_ACTIVE_WHERE = text("status IN ('queued', 'running')")


class AIJob(Base):
    __tablename__ = "ai_jobs"

    id = Column(Integer, primary_key=True)
    user_id = Column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind = Column(String(20), nullable=False)  # tailor|recheck|extract|suggest|match
    # draft id (tailor/recheck), profile id (extract/suggest), job id (match)
    subject_id = Column(Integer, nullable=False)
    # Lower claims first (ai_queue.PRIORITY_*): interactive = 0
    priority_class = Column(Integer, nullable=False, default=0)
    status = Column(String(12), nullable=False, default="queued")  # queued|running|done|failed
    attempts = Column(Integer, nullable=False, default=0)
    next_attempt_at = Column(DateTime, nullable=False, default=utc_now)
    lease_until = Column(DateTime, nullable=True)
    lease_token = Column(String(36), nullable=True)
    # Last load signal (e.g. '1302', 'timeout', '5xx', 'quota:1308') —
    # observability + the "paused until" copy; never shown raw.
    last_error_code = Column(String(40), nullable=True)
    error = Column(Text, nullable=True)  # user-facing, failed rows only
    params = Column(Text, nullable=True)  # JSON (suggest: country/mode)
    result = Column(Text, nullable=True)  # JSON (suggest's suggestions)
    enqueued_at = Column(DateTime, nullable=False, default=utc_now)
    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "uq_ai_jobs_active_subject",
            "kind",
            "user_id",
            "subject_id",
            unique=True,
            sqlite_where=_ACTIVE_WHERE,
            postgresql_where=_ACTIVE_WHERE,
        ),
        Index("ix_ai_jobs_claim", "status", "priority_class", "next_attempt_at"),
    )

    def __repr__(self):
        return f"<AIJob {self.id} {self.kind}:{self.subject_id} {self.status}>"
