"""
Remotive scraper — free public remote jobs API.

API: https://remotive.com/api/remote-jobs?limit=N
No key required. Returns {jobs: [...]} with id, url, title, company_name,
category, job_type, candidate_required_location, salary, description (HTML),
publication_date (ISO).
"""

import logging
from datetime import datetime
from typing import List, Optional

from app.services.scrapers.base import BaseScraper, NormalizedJob, strip_html

logger = logging.getLogger(__name__)

API_URL = "https://remotive.com/api/remote-jobs"
LIMIT = 100


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class RemotiveScraper(BaseScraper):
    source = "remotive"

    def fetch(self, context=None) -> List[NormalizedJob]:
        data = self.get_json(API_URL, params={"limit": LIMIT})
        jobs: List[NormalizedJob] = []

        for item in data.get("jobs", []):
            try:
                jobs.append(
                    NormalizedJob(
                        source=self.source,
                        source_id=str(item.get("id")),
                        title=item.get("title", "Untitled"),
                        company=item.get("company_name"),
                        location=item.get("candidate_required_location"),
                        remote=True,  # Remotive is remote-only
                        url=item.get("url", ""),
                        description=strip_html(item.get("description")),
                        employment_type=item.get("job_type"),
                        salary=item.get("salary"),
                        tags=[item["category"]] if item.get("category") else [],
                        category=item.get("category"),
                        published_at=_parse_date(item.get("publication_date")),
                    )
                )
            except Exception as e:
                logger.warning("[%s] skipping malformed job: %s", self.source, e)

        logger.info("[%s] fetched %d jobs", self.source, len(jobs))
        return jobs
