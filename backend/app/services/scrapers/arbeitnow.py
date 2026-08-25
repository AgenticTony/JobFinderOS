"""
Arbeitnow scraper — free public job board API (Germany/EU focused).

API: https://www.arbeitnow.com/api/job-board-api
No key required. Returns {data: [...]} with slug, company_name, title,
description (HTML), remote, url, tags, job_types, location, created_at (unix).
"""

import logging
from datetime import datetime, timezone
from typing import List

from app.services.scrapers.base import BaseScraper, NormalizedJob, strip_html

logger = logging.getLogger(__name__)

API_URL = "https://www.arbeitnow.com/api/job-board-api"


class ArbeitnowScraper(BaseScraper):
    source = "arbeitnow"

    def fetch(self, context=None) -> List[NormalizedJob]:
        data = self.get_json(API_URL)
        jobs: List[NormalizedJob] = []

        for item in data.get("data", []):
            try:
                description = strip_html(item.get("description"))
                job_types = item.get("job_types") or []
                created_at = item.get("created_at")
                published_at = None
                if isinstance(created_at, (int, float)):
                    published_at = datetime.fromtimestamp(created_at, tz=timezone.utc).replace(tzinfo=None)

                jobs.append(
                    NormalizedJob(
                        source=self.source,
                        source_id=item.get("slug"),
                        title=item.get("title", "Untitled"),
                        company=item.get("company_name"),
                        location=item.get("location"),
                        remote=bool(item.get("remote")),
                        url=item.get("url", ""),
                        description=description,
                        employment_type=", ".join(job_types) if job_types else None,
                        tags=item.get("tags") or [],
                        published_at=published_at,
                    )
                )
            except Exception as e:  # tolerate malformed entries
                logger.warning("[%s] skipping malformed job: %s", self.source, e)

        logger.info("[%s] fetched %d jobs", self.source, len(jobs))
        return jobs
