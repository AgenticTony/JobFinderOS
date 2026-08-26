"""
Working Nomads scraper — free public remote jobs API.

API: https://www.workingnomads.com/api/exposed_jobs/
No key required. Returns a list of jobs with id, title, company_name, url,
location, description (HTML), pub_date, category_name.
"""

import logging
import re
from datetime import datetime
from typing import List, Optional

from app.services.scrapers.base import (
    BaseScraper,
    NormalizedJob,
    extract_apply_email,
    strip_html,
)

logger = logging.getLogger(__name__)

API_URL = "https://www.workingnomads.com/api/exposed_jobs/"


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


class WorkingNomadsScraper(BaseScraper):
    source = "workingnomads"

    def fetch(self, context=None) -> List[NormalizedJob]:
        data = self.get_json(API_URL)
        if not isinstance(data, list):
            logger.warning("[%s] unexpected API shape: %s", self.source, type(data))
            return []

        jobs: List[NormalizedJob] = []
        for item in data:
            try:
                description = strip_html(item.get("description"))
                apply_email = item.get("application_email") or extract_apply_email(description)
                # The API exposes no id field — derive it from the URL slug (/job/go/12345/)
                source_id = _slug_from_url(item.get("url"))
                tags = item.get("tags")
                if isinstance(tags, str):
                    tags = [t.strip() for t in tags.split(",") if t.strip()]
                elif not isinstance(tags, list):
                    tags = []
                jobs.append(
                    NormalizedJob(
                        source=self.source,
                        source_id=source_id,
                        title=item.get("title", "Untitled"),
                        company=item.get("company_name"),
                        location=item.get("location"),
                        remote=True,  # Working Nomads is remote-focused
                        url=item.get("url", ""),
                        description=description,
                        tags=tags,
                        category=item.get("category_name"),
                        application_email=apply_email,
                        published_at=_parse_date(item.get("pub_date")),
                    )
                )
            except Exception as e:
                logger.warning("[%s] skipping malformed job: %s", self.source, e)

        logger.info("[%s] fetched %d jobs", self.source, len(jobs))
        return jobs


def _slug_from_url(url: Optional[str]) -> Optional[str]:
    """Extract the trailing numeric id from a workingnomads job URL."""
    if not url:
        return None
    match = re.search(r"/(\d+)/?$", url.rstrip("/"))
    return match.group(1) if match else None
