"""Pipeline schemas for JobFinderOS."""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.schemas.match import MatchWithJobResponse
from app.services.scrapers import SCRAPER_REGISTRY


class PipelineRunRequest(BaseModel):
    sources: Optional[List[str]] = None  # default: all enabled sources
    match: bool = True  # run AI matching after scraping
    @field_validator("sources")
    @classmethod
    def _sources_must_exist(cls, v):
        """Client-controlled source names are validated against the
        registry at the boundary — a removed/misspelled scraper gets an
        explicit 422 naming the valid sources, never a silently dropped
        request or a failed ScrapeRun per hunt."""
        if v is None:
            return v
        unknown = [s for s in v if s not in SCRAPER_REGISTRY]
        if unknown:
            raise ValueError(
                f"unknown source(s) {unknown} — valid: {sorted(SCRAPER_REGISTRY)}"
            )
        return v

    # Server-clamped: this caps AI calls per run, and the rate limiter
    # buckets RUNS (12/hour), not spend — an unbounded client value is a
    # cost-DoS vector (POST {"max_matches": 100000} twelve times an hour
    # from one authenticated account). The ceiling is the server's
    # MAX_JOBS_PER_MATCH_RUN, not anything the client sends.
    max_matches: Optional[int] = Field(
        None, ge=1, le=settings.MAX_JOBS_PER_MATCH_RUN
    )


class ScrapeSummary(BaseModel):
    source: str
    status: str
    jobs_found: int = 0
    jobs_new: int = 0
    error: Optional[str] = None


class MatchSummary(BaseModel):
    status: str
    jobs_considered: int = 0
    matches_created: int = 0
    skipped_no_profile: bool = False
    error: Optional[str] = None


class PipelineRunResponse(BaseModel):
    scrape: List[ScrapeSummary] = []
    match: Optional[MatchSummary] = None
    top_matches: List[MatchWithJobResponse] = []
