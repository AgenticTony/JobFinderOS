"""
JobTech / Platsbanken scraper — Arbetsförmedlingen's official open job API.

Covers essentially the entire Swedish job market: employer-direct postings AND
staffing agencies (Manpower, Adecco, Randstad, Academic Work, Uniflex…) all
post their listings to Platsbanken. This is the single best Swedish source.

API: https://jobsearch.api.jobtechdev.se/search (JobTech, Arbetsförmedlingen)
Docs: https://jobtechdev.se — free API key recommended for production use;
light use works keyless. Send the key as the `api-key` header.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.scrapers.base import BaseScraper, NormalizedJob

logger = logging.getLogger(__name__)

API_URL = "https://jobsearch.api.jobtechdev.se/search"
PER_QUERY_LIMIT = 100  # API max page size


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.split("+")[0].split(".")[0])
    except ValueError:
        return None


class JobtechScraper(BaseScraper):
    source = "jobtech"

    @classmethod
    def is_configured(cls, context: Optional[dict] = None) -> bool:
        # Needs queries from SOMEWHERE: the user's onboarding (preferred) or
        # the global override. Empty = wait for onboarding rather than scrape
        # the wrong profession's jobs (Platsbanken is ALL trades).
        return bool(cls._queries(context))

    @staticmethod
    def _queries(context: Optional[dict]) -> List[str]:
        if context and context.get("queries"):
            return [str(q).strip() for q in context["queries"] if str(q).strip()]
        return [q.strip() for q in settings.JOBTECH_QUERIES.split(",") if q.strip()]

    def fetch(self, context: Optional[dict] = None) -> List[NormalizedJob]:
        jobs: List[NormalizedJob] = []
        seen: set = set()

        headers = {"accept": "application/json"}
        if settings.JOBTECH_API_KEY:
            headers["api-key"] = settings.JOBTECH_API_KEY

        queries = self._queries(context)
        for query in queries:
            try:
                response = httpx.get(
                    API_URL,
                    params={"q": query, "limit": PER_QUERY_LIMIT},
                    headers=headers,
                    timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                    follow_redirects=True,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning("[jobtech] query '%s' failed: %s", query, e)
                continue

            for hit in data.get("hits", []):
                job = self._normalize(hit)
                if job and job.source_id not in seen:
                    seen.add(job.source_id)
                    jobs.append(job)

        logger.info("[jobtech] fetched %d jobs from %d queries", len(jobs), len(queries))
        return jobs

    @staticmethod
    def _normalize(hit: Dict[str, Any]) -> Optional[NormalizedJob]:
        try:
            if hit.get("removed"):
                return None

            description = (hit.get("description") or {}).get("text")
            employer = (hit.get("employer") or {})
            address = (hit.get("workplace_address") or {})
            application = (hit.get("application_details") or {})
            occupation = (hit.get("occupation") or {})
            field = (hit.get("occupation_field") or {})

            location_parts = [
                address.get("municipality"),
                address.get("region") if address.get("municipality") != address.get("region") else None,
            ]
            tags = [t for t in (occupation.get("label"), field.get("label")) if t]

            employment_bits = [
                (hit.get("working_hours_type") or {}).get("label"),
                (hit.get("duration") or {}).get("label"),
            ]

            return NormalizedJob(
                source="jobtech",
                source_id=str(hit.get("id")),
                title=hit.get("headline") or "Unnamed position",
                company=employer.get("name") or employer.get("workplace"),
                location=", ".join(p for p in location_parts if p) or None,
                url=hit.get("webpage_url") or "",
                description=description,
                employment_type=" · ".join(b for b in employment_bits if b) or None,
                tags=tags,
                category=field.get("label"),
                application_email=application.get("email"),
                application_url=application.get("url"),
                published_at=_parse_dt(hit.get("publication_date")),
            )
        except Exception as e:
            logger.warning("[jobtech] skipping malformed hit: %s", e)
            return None
