"""
ApplicationDraft model for JobFinderOS.

The stage between match approval and sending: the AI tailors the user's CV
and cover letter to the approved job, the user reviews and edits both, and
only then is the application submitted.
"""

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import relationship

from app.core.orm import Base
from app.core.timeutil import utc_now


class ApplicationDraft(Base):
    """A tailored application package awaiting user review."""

    __tablename__ = "application_drafts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("job_postings.id"), nullable=False, index=True)
    match_id = Column(Integer, ForeignKey("match_results.id"), nullable=True)

    # The tailored package (editable by the user before submission)
    cover_letter = Column(Text, nullable=True)  # First person, addressed to the employer
    tailored_cv = Column(Text, nullable=True)  # Restructured CV text for THIS job
    changes_summary = Column(Text, nullable=True)  # What the AI changed and why ("you" voice)

    # P1-5b: the CV this package was tailored FROM, snapshotted at draft
    # creation. profile.cv_file_path moves on re-upload; a package guarded
    # against CV-old must send CV-old as its "original CV" — an
    # old-tailored package with CV-new attached is internally
    # contradictory. NULL on legacy rows -> the send path falls back to
    # the profile's current path (the previous behavior).
    cv_file_path = Column(String(500), nullable=True)
    cv_hash = Column(String(64), nullable=True)  # sha256 of profile.cv_text at tailoring time

    # Lifecycle: drafting -> ready -> sending -> submitted | failed -> ready
    # ('sending' is the transient dispatch claim taken by submit_draft —
    # see draft_service; a crashed dispatch is recovered by the
    # maintenance sweep, never by a second send)
    status = Column(String(20), default="drafting", nullable=False)
    error = Column(Text, nullable=True)

    # WO-01 fabrication guard: Layer A findings at draft creation, plus
    # retry/block counts so the fabrication rate is a measured number
    # (advisory findings render in the review UI; high-confidence ones
    # drove regeneration before these were recorded)
    fabrication_findings = Column(Text, nullable=True)  # JSON list
    fabrication_retries = Column(Integer, default=0, nullable=False)
    fabrication_blocked = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="drafts")

    def __repr__(self):
        return f"<ApplicationDraft job={self.job_id} status={self.status}>"
