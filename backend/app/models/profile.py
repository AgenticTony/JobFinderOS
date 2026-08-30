"""
Profile model for JobFinderOS.

Inverse of TalentHive's Candidate: instead of many candidates per job,
there is ONE job seeker with a CV on file, matched against many jobs.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, Uuid

from app.core.orm import Base
from app.core.timeutil import utc_now


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
    municipality = Column(String(255), nullable=True)  # e.g. "Malmö" (legacy single)
    # JSON array — strict multi-municipality location scope (user decision:
    # picking Malmö means Malmö; add Lund for the commute belt). Empty +
    # no legacy value = explicit whole-region.
    municipalities = Column(Text, nullable=True)  # JSON array of strings
    # Commute-zone radius around the first chosen municipality (km, 0/NULL
    # = exact municipality match only). JobTech fetches switch to
    # position + position.radius when this is set and a centroid resolves.
    search_radius_km = Column(Integer, nullable=True)
    remote_only = Column(Integer, default=0, nullable=False)  # 1 = drop on-site jobs
    include_remote = Column(Integer, default=0, nullable=False)  # 1 = opt in to worldwide remote jobs
    search_queries = Column(Text, nullable=True)  # JSON array — AI-suggested, user-approved
    # JSON array of {"code","label"} — Arbetsförmedlingen occupation-name
    # concepts (validated server-side against the taxonomy feed). Fetching
    # by code catches ads whose title never contains the free-text query.
    occupation_codes = Column(Text, nullable=True)
    languages = Column(Text, nullable=True)  # JSON array — languages the user works in

    user_id = Column(Uuid, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    # is_active kept for the migration backfill only; per-user semantics
    # replace the singleton (one profile per user via the unique FK)
    is_active = Column(Integer, default=1, nullable=False)
    created_at = Column(DateTime, default=utc_now, nullable=False)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    def __repr__(self):
        return f"<Profile {self.full_name or self.id}>"
