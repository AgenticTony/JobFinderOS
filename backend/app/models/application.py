"""
Application model for JobFinderOS.

Tracks the apply stage: after the user approves a match, an application
is created and executed via email (Resend/SMTP) or queued for browser/manual apply.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.core.database import Base


class Application(Base):
    """An application to a job, created after user approval of a match."""

    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
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

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships
    job = relationship("JobPosting", backref="applications")

    def __repr__(self):
        return f"<Application job={self.job_id} method={self.method} status={self.status}>"
