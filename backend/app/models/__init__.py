"""SQLAlchemy models for JobFinderOS."""

from app.models.ai_usage import AIUsage  # noqa: F401 — registered on Base
from app.models.application import Application
from app.models.draft import ApplicationDraft
from app.models.job import JobPosting
from app.models.match import MatchResult
from app.models.profile import Profile
from app.models.scrape_run import ScrapeRun
from app.models.user import User

__all__ = [
    "Profile",
    "JobPosting",
    "MatchResult",
    "ApplicationDraft",
    "Application",
    "ScrapeRun",
    "User",
]
