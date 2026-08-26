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
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=True, index=True)
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

    # Metadata (TalentHive pattern)
    model_used = Column(String(50), nullable=True)
    processing_time_ms = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="match_result")

    def __repr__(self):
        return f"<MatchResult {self.tier} score={self.score} job={self.job_id}>"
