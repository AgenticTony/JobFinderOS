"""
Application model for JobFinderOS.

Tracks the apply stage: after the user approves a match, an application
is created and executed via email (Resend/SMTP) or queued for browser/manual apply.
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
from sqlalchemy.orm import relationship

from app.core.orm import Base
from app.core.timeutil import utc_now


class Application(Base):
    """An application to a job, created after user approval of a match."""

    __tablename__ = "applications"
    # SUBMIT: one application row per draft, enforced by the database. The
    # partial WHERE keeps draft-less applies (browser/manual history seeded
    # without a draft) unaffected — multiple NULLs are allowed on both
    # SQLite and Postgres.
    __table_args__ = (
        Index(
            "uq_applications_draft_id", "draft_id", unique=True,
            sqlite_where=text("draft_id IS NOT NULL"),
            postgresql_where=text("draft_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("match_results.id"), nullable=True)
    draft_id = Column(Integer, ForeignKey("application_drafts.id"), nullable=True)

    # How the application is delivered
    method = Column(String(20), nullable=False)  # email, browser, manual

    # Lifecycle: queued -> sent | failed | manual_pending
    status = Column(String(20), default="queued", nullable=False)
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=True)  # Cover note / email body sent
    target_email = Column(String(255), nullable=True)
    apply_url = Column(String(1000), nullable=True)

    sent_at = Column(DateTime, nullable=True)
    error = Column(Text, nullable=True)

    # WO-20 outcome layer — what the EMPLOYER did. A second lifecycle
    # parallel to send-side `status` above; outcome writes never touch
    # `status` (tested). Vocabulary + Final/Open partition live in
    # app/services/outcome_service.py — the single source of truth.
    outcome_status = Column(String(20), nullable=True)  # outcome_service.OUTCOME_STATUSES
    outcome_source = Column(String(20), nullable=True)  # manual | reply_detection | sweep
    outcome_note = Column(Text, nullable=True)
    # Evidence POINTER (message id / citation) — never a message body
    outcome_evidence = Column(String(500), nullable=True)
    # WHEN this outcome became established: the reply's arrival time when
    # evidence provides one (approved proposals), the recording time for
    # manual markers. Set on every outcome write — an open `interview`
    # has a first-response time too (that's the stat), not only finals.
    resolved_at = Column(DateTime, nullable=True)

    # WO-20 follow-up chain: a follow-up send points at the application
    # it follows up on. Counted for the 10-day nudge (< 2 follow-ups).
    follow_up_of_id = Column(Integer, ForeignKey("applications.id"), nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="applications")

    def __repr__(self):
        return f"<Application job={self.job_id} method={self.method} status={self.status}>"
