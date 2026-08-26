"""
ApplicationDraft model for JobFinderOS.

The stage between match approval and sending: the AI tailors the user's CV
and cover letter to the approved job, the user reviews and edits both, and
only then is the application submitted.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base
from app.core.timeutil import utc_now


class ApplicationDraft(Base):
    """A tailored application package awaiting user review."""

    __tablename__ = "application_drafts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("match_results.id"), nullable=True)

    # The tailored package (editable by the user before submission)
    cover_letter = Column(Text, nullable=True)  # First person, addressed to the employer
    tailored_cv = Column(Text, nullable=True)  # Restructured CV text for THIS job
    changes_summary = Column(Text, nullable=True)  # What the AI changed and why ("you" voice)

    # Lifecycle: drafting -> ready -> submitted | failed (retryable)
    status = Column(String(20), default="drafting", nullable=False)
    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="drafts")

    def __repr__(self):
        return f"<ApplicationDraft job={self.job_id} status={self.status}>"
