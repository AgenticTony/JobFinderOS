"""
JobPosting model for JobFinderOS.

Extends TalentHive's Job with scrape provenance and application channels.
Jobs arrive from scrapers (Arbeitnow, Remotive, Jobicy, Working Nomads)
or manual entry, then flow through: new -> matched -> approved/rejected -> applied.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class JobPosting(Base):
    """A scraped or manually added job posting."""

    __tablename__ = "job_postings"

    id = Column(Integer, primary_key=True, index=True)

    # Provenance — identifies the job across scrape runs
    source = Column(String(50), nullable=False, index=True)  # arbeitnow, remotive, jobicy, workingnomads, manual
    source_id = Column(String(255), nullable=True)  # ID/slug from the source site

    # Job content
    title = Column(String(500), nullable=False)
    company = Column(String(255), nullable=True)
    location = Column(String(255), nullable=True)
    remote = Column(Integer, default=0, nullable=False)  # 1/0
    dedupe_key = Column(String(16), nullable=True, index=True)  # cross-board title+company key
    url = Column(String(1000), nullable=False)  # job posting URL
    description = Column(Text, nullable=True)  # Full job description text
    employment_type = Column(String(50), nullable=True)  # full-time, part-time, contract
    salary = Column(String(255), nullable=True)
    tags = Column(Text, nullable=True)  # JSON array of strings
    category = Column(String(255), nullable=True)

    # How to apply
    application_email = Column(String(255), nullable=True)  # direct email apply
    application_url = Column(String(1000), nullable=True)  # ATS/portal apply link

    # Workflow status
    status = Column(String(20), default="new", nullable=False, index=True)
    # new -> matched -> approved | rejected | dismissed -> applied

    published_at = Column(DateTime, nullable=True)
    scraped_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # Relationships are defined on MatchResult (backref: match_result) and Application (backref: applications)

    def __repr__(self):
        return f"<JobPosting {self.title} @ {self.company} [{self.source}]>"
