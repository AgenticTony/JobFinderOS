"""
ScrapeRun model for JobFinderOS — audit trail for scraper executions.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text

from app.core.database import Base


class ScrapeRun(Base):
    """One execution of a scraper source (or the full pipeline)."""

    __tablename__ = "scrape_runs"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False, index=True)  # source name or "pipeline"
    status = Column(String(20), default="running", nullable=False)  # running, completed, failed
    jobs_found = Column(Integer, default=0, nullable=False)
    jobs_new = Column(Integer, default=0, nullable=False)
    matches_created = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)

    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ScrapeRun {self.source} {self.status}>"
