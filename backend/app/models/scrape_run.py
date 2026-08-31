"""
ScrapeRun model for JobFinderOS — audit trail for scraper executions.
"""

from sqlalchemy import Column, DateTime, Index, Integer, String, Text

from app.core.orm import Base
from app.core.timeutil import utc_now


class ScrapeRun(Base):
    """One execution of a scraper source (or the full pipeline)."""

    __tablename__ = "scrape_runs"
    # WO-14 review fix: the cooldown keys on the fetch identity
    # (source, scope) — the same key the watermarks use — so one user's
    # manual hunt never suppresses a different-scope hunt.
    __table_args__ = (
        Index("ix_scrape_runs_source_scope", "source", "scope"),
    )

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String(50), nullable=False, index=True)  # source name or "pipeline"
    status = Column(String(20), default="running", nullable=False)  # running, completed, failed
    # WO-14: the scrape context's scope key (pipeline._scope_key) —
    # which municipalities/queries/region shaped this fetch. NULL on
    # legacy rows written before the column existed.
    scope = Column(String(255), nullable=True)
    jobs_found = Column(Integer, default=0, nullable=False)
    jobs_new = Column(Integer, default=0, nullable=False)
    matches_created = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)

    started_at = Column(DateTime, default=utc_now, nullable=False)
    finished_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ScrapeRun {self.source} {self.status}>"
