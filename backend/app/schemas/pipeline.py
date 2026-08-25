"""Pipeline schemas for JobFinderOS."""

from typing import List, Optional

from pydantic import BaseModel

from app.schemas.match import MatchWithJobResponse


class PipelineRunRequest(BaseModel):
    sources: Optional[List[str]] = None  # default: all enabled sources
    match: bool = True  # run AI matching after scraping
    max_matches: Optional[int] = None  # cap AI calls this run


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
