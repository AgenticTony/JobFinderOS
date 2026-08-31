"""
Scraper framework for JobFinderOS.

Each source implements BaseScraper and returns NormalizedJob records.
Deduplication happens in the CRUD layer via (source, source_id) / URL.

Sources are free public job APIs — no API keys, respectful rate limits.
Add a new source by subclassing BaseScraper and registering it in
app/services/scrapers/__init__.py::SCRAPER_REGISTRY.
"""

import html
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel, Field

from app.core.config import settings

logger = logging.getLogger(__name__)


class NormalizedJob(BaseModel):
    """Source-independent job record produced by every scraper."""

    source: str
    source_id: Optional[str] = None
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote: bool = False
    url: str
    description: Optional[str] = None
    employment_type: Optional[str] = None
    salary: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    category: Optional[str] = None
    application_email: Optional[str] = None
    application_url: Optional[str] = None
    published_at: Optional[datetime] = None


_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"[ \t]+")
_NEWLINES_RE = re.compile(r"\n{3,}")
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")


def strip_html(raw: Optional[str], max_length: int = 12000) -> Optional[str]:
    """Convert HTML job descriptions to plain text (stdlib only)."""
    if not raw:
        return raw
    text = _TAG_RE.sub(" ", raw)
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text)
    text = _NEWLINES_RE.sub("\n\n", text)
    text = text.strip()
    if len(text) > max_length:
        text = text[:max_length]
    return text


def extract_apply_email(text: Optional[str]) -> Optional[str]:
    """Find an application email address inside a job description."""
    if not text:
        return None
    for match in _EMAIL_RE.findall(text):
        lowered = match.lower()
        # Avoid images/trackers; prefer addresses that look like real inboxes
        if any(ext in lowered for ext in (".png", ".jpg", ".gif", ".webp")):
            continue
        return match
    return None


class BaseScraper(ABC):
    """Base class for job source scrapers."""

    #: registry name — must be unique
    source: str = "base"

    #: PIPE-17 fetch-health report. True = every page this fetch was
    #: going to read came back (the result list is the WHOLE unit).
    #: Paginated scrapers that bail out mid-walk on a page error set
    #: this False on their own instance — the pipeline then refuses to
    #: advance the delta watermark, because stamping a partial fetch
    #: permanently skips the un-read pages. Single-request scrapers
    #: never touch it: for them a fetch either succeeds whole or raises
    #: (which fails the run, and a failed run never watermarks).
    fetch_complete: bool = True

    @classmethod
    def is_configured(cls, context: Optional[dict] = None) -> bool:
        """False when required credentials/settings are missing — the pipeline
        then skips the source with a clear message instead of failing.
        `context` carries per-user onboarding settings (country, queries,
        location) for sources whose readiness depends on them."""
        return True

    @abstractmethod
    def fetch(self, context: Optional[dict] = None) -> List[NormalizedJob]:
        """Fetch jobs from the source and normalize them.

        context (all keys optional): country, region, municipality,
        remote_only, queries — from the active profile's onboarding.
        """

    def get_json(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """GET a JSON API endpoint with the configured timeout."""
        response = httpx.get(
            url,
            params=params,
            timeout=settings.SCRAPE_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "JobFinderOS/0.1 (job-search-automation)"},
        )
        response.raise_for_status()
        return response.json()

    def __repr__(self):
        return f"<Scraper {self.source}>"
