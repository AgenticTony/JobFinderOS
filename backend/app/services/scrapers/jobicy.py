"""
Jobicy scraper — free public remote jobs API.

API: https://jobicy.com/api/v2/remote-jobs?count=N
No key required. Returns {jobs: [...]} with id, url, jobTitle, companyName,
jobGeo, jobLevel, jobDescription (HTML), jobExcerpt, industry, pubDate.
"""

import logging
from datetime import datetime
from typing import List, Optional

from app.services.scrapers.base import (
    BaseScraper,
    NormalizedJob,
    extract_apply_email,
    strip_html,
)

logger = logging.getLogger(__name__)

API_URL = "https://jobicy.com/api/v2/remote-jobs"
COUNT = 50


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class JobicyScraper(BaseScraper):
    source = "jobicy"

    def fetch(self, context=None) -> List[NormalizedJob]:
        data = self.get_json(API_URL, params={"count": COUNT})
        jobs: List[NormalizedJob] = []

        for item in data.get("jobs", []):
            try:
                description = strip_html(
                    item.get("jobDescription") or item.get("jobExcerpt")
                )
                jobs.append(
                    NormalizedJob(
                        source=self.source,
                        source_id=str(item.get("id")),
                        title=item.get("jobTitle", "Untitled"),
                        company=item.get("companyName"),
                        location=item.get("jobGeo"),
                        remote=True,
                        url=item.get("url", ""),
                        description=description,
                        employment_type=None,
                        tags=[item["jobLevel"]] if item.get("jobLevel") else [],
                        category=item.get("industry"),
                        application_email=extract_apply_email(description),
                        published_at=_parse_date(item.get("pubDate")),
                    )
                )
            except Exception as e:
                logger.warning("[%s] skipping malformed job: %s", self.source, e)

        logger.info("[%s] fetched %d jobs", self.source, len(jobs))
        return jobs
