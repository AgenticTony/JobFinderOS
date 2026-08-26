"""
MatchResult model for JobFinderOS.

The job-seeker-direction inversion of TalentHive's Screening model:
instead of "is this candidate right for the job", it stores
"is this job right for me" — with an apply recommendation and cover note.
"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.timeutil import utc_now


class MatchResult(Base):
    """AI match assessment of one job against the active profile's CV."""

    __tablename__ = "match_results"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=False, index=True)
    __table_args__ = (
        UniqueConstraint("user_id", "job_id", name="uq_match_results_user_job"),
    )

    # Match results — tier system adapted from TalentHive's three tiers
    score = Column(Integer, nullable=False)  # 0-100
    tier = Column(String(20), nullable=False)  # excellent_match, good_match, stretch, poor_match
    reasoning = Column(Text, nullable=True)
    matched_skills = Column(Text, nullable=True)  # JSON array
    missing_skills = Column(Text, nullable=True)  # JSON array
    transferable_skills = Column(Text, nullable=True)  # JSON array — TalentHive differentiator kept
    recommendation = Column(String(20), nullable=True)  # apply, maybe, skip
    cover_note = Column(Text, nullable=True)  # Short tailored note for the application
    confidence = Column(String(10), default="medium", nullable=True)  # high, medium, low

    # Decision tracking (the approval workflow)
    decision = Column(String(20), nullable=True)  # approved, rejected — NULL = pending review
    decided_at = Column(DateTime, nullable=True)

    # Per-user dismissal. NULL = a live match the user should see.
    # Set = "this user's pipeline evaluated and dropped it" — invisible in
    # the queue, but it stops re-evaluation and keeps the audit trail.
    # Dismissal MUST live here and never on job_postings.status: the job row
    # is shared, so writing one user's exclude-keyword or duplicate decision
    # onto it hid the posting from every other user.
    # Values: excluded_keyword | duplicate | no_description | below_threshold
    dismissed_reason = Column(String(30), nullable=True, index=True)

    # Metadata (TalentHive pattern)
    model_used = Column(String(50), nullable=True)
    # Which scoring prompt produced this score. Scores from different prompt
    # versions are NOT comparable — the backlog was scored before the rubric
    # anchors landed and re-running the SAME model on the SAME job moved
    # scores by up to 26 points. NULL = pre-versioning (stale).
    prompt_version = Column(String(32), nullable=True, index=True)
    processing_time_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="match_result")

    def __repr__(self):
        return f"<MatchResult {self.tier} score={self.score} job={self.job_id}>"
