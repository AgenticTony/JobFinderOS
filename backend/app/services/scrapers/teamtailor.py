"""
Teamtailor scraper — the dominant career-site platform in Sweden/Nordics.

Hundreds of Swedish employers and staffing agencies run their career pages on
Teamtailor ({company}.teamtailor.com), which exposes a public JSON feed at
/jobs.json (JSON Feed format: root title = company, items = jobs).

Configure the sites to scrape via TEAMTAILOR_SITES=slug1,slug2 (for example
TEAMTAILOR_SITES=manpower,fortnoxab). Not configured -> source is skipped.
"""

import logging
from datetime import datetime
from typing import List, Optional

import httpx

from app.core.config import settings
from app.services.scrapers.base import BaseScraper, NormalizedJob, strip_html

logger = logging.getLogger(__name__)


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.split("+")[0].split(".")[0])
    except ValueError:
        return None


class TeamtailorScraper(BaseScraper):
    source = "teamtailor"

    @classmethod
    def is_configured(cls, context=None) -> bool:
        return bool(settings.TEAMTAILOR_SITES.strip())

    def sites(self) -> List[str]:
        return [s.strip() for s in settings.TEAMTAILOR_SITES.split(",") if s.strip()]

    def fetch(self, context=None) -> List[NormalizedJob]:
        jobs: List[NormalizedJob] = []
        for slug in self.sites():
            try:
                jobs.extend(self._fetch_site(slug))
            except Exception as e:
                logger.warning("[teamtailor] site '%s' failed: %s", slug, e)
        logger.info("[teamtailor] fetched %d jobs from %d sites", len(jobs), len(self.sites()))
        return jobs

    def _fetch_site(self, slug: str) -> List[NormalizedJob]:
        response = httpx.get(
            f"https://{slug}.teamtailor.com/jobs.json",
            timeout=settings.SCRAPE_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers={"User-Agent": "JobFinderOS/0.1 (job-search-automation)"},
        )
        response.raise_for_status()
        feed = response.json()

        company = feed.get("title") or slug
        site_jobs: List[NormalizedJob] = []
        for item in feed.get("items", []):
            try:
                site_jobs.append(
                    NormalizedJob(
                        source=self.source,
                        source_id=f"{slug}:{item.get('id')}",
                        title=item.get("title") or "Untitled",
                        company=company,
                        remote=True,  # Teamtailor feeds don't flag location; posting has details
                        url=item.get("url") or f"https://{slug}.teamtailor.com",
                        description=strip_html(item.get("content_html")),
                        published_at=_parse_dt(item.get("date_published")),
                    )
                )
            except Exception as e:
                logger.warning("[teamtailor] '%s' skipping malformed item: %s", slug, e)
        return site_jobs
