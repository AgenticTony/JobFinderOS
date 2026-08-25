"""
Profile model for JobFinderOS.

Inverse of TalentHive's Candidate: instead of many candidates per job,
there is ONE job seeker with a CV on file, matched against many jobs.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class Profile(Base):
    """The job seeker — CV on file plus AI-extracted structured data."""

    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)

    # Contact info (used for applications)
    full_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(50), nullable=True)
    location = Column(String(255), nullable=True)
    professional_title = Column(String(255), nullable=True)

    # CV data — IMMUTABLE after upload: the permanent reference point.
    # Job-specific tailored versions live in ApplicationDraft rows, never here.
    cv_text = Column(Text, nullable=True)  # Extracted text from CV
    cv_file_path = Column(String(500), nullable=True)  # Local path to stored CV file
    cv_file_name = Column(String(255), nullable=True)
    cv_file_size = Column(Integer, nullable=True)

    # AI-extracted structured data (JSON text — TalentHive raw_* pattern)
    skills = Column(Text, nullable=True)  # JSON array of {name, years, level}
    experience_years = Column(Integer, nullable=True)
    recent_roles = Column(Text, nullable=True)  # JSON array of {title, company, period, highlights}
    education = Column(Text, nullable=True)  # JSON array
    certifications = Column(Text, nullable=True)  # JSON array
    keywords = Column(Text, nullable=True)  # JSON array — matching keywords
    ai_summary = Column(Text, nullable=True)  # Short professional summary

    # Seeker preferences
    preferred_roles = Column(Text, nullable=True)  # JSON array of strings
    preferred_locations = Column(String(500), nullable=True)
    remote_ok = Column(Integer, default=1, nullable=False)  # 1/0
    min_salary = Column(String(100), nullable=True)
    exclude_keywords = Column(Text, nullable=True)  # JSON array — jobs to skip

    # Onboarding — per-user targeting (drives source pack, queries, location filter)
    onboarded = Column(Integer, default=0, nullable=False)  # 1 after wizard completes
    country = Column(String(2), nullable=True)  # ISO code: SE, GB
    region = Column(String(255), nullable=True)  # e.g. "Skåne län", "Greater London"
    municipality = Column(String(255), nullable=True)  # e.g. "Malmö"
    remote_only = Column(Integer, default=0, nullable=False)  # 1 = drop on-site jobs
    include_remote = Column(Integer, default=0, nullable=False)  # 1 = opt in to worldwide remote jobs
    search_queries = Column(Text, nullable=True)  # JSON array — AI-suggested, user-approved
    languages = Column(Text, nullable=True)  # JSON array — languages the user works in

    is_active = Column(Integer, default=1, nullable=False)  # single active profile
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Profile {self.full_name or self.id}>"
