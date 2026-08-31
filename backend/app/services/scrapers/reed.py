"""
Reed.co.uk scraper — UK job board, official jobs-search API.

Docs (Jobs - Search / Jobs - Details): the job-seeker side of Reed's API.
Auth: API key as the USERNAME of a basic-auth header, empty password.
Limit: 2,000 requests/hour, max 100 results per search page.

Gated behind REED_API_KEY — not configured -> source is skipped with a clear
message. Keywords/location become per-user fields at onboarding time; the
global config below is a manual override for testing.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import settings
from app.services.scrapers.base import BaseScraper, NormalizedJob, strip_html

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.reed.co.uk/api/1.0/search"
DETAILS_URL = "https://www.reed.co.uk/api/1.0/jobs/{job_id}"
RESULTS_PER_QUERY = 100  # API maximum


def _parse_uk_date(raw: Optional[str]) -> Optional[datetime]:
    """Reed dates come as dd/mm/yyyy strings."""
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%d/%m/%Y")
    except (ValueError, TypeError):
        return None


def _fmt_sal(value) -> str:
    """34350.0 -> 34,350 ; keeps pence when present (caller adds the £)."""
    try:
        f = float(value)
        return f"{int(f):,}" if f == int(f) else f"{f:,.2f}"
    except (TypeError, ValueError):
        return str(value)


class ReedScraper(BaseScraper):
    source = "reed"

    @classmethod
    def is_configured(cls, context: Optional[dict] = None) -> bool:
        return bool(settings.REED_API_KEY)

    def _auth(self) -> httpx.BasicAuth:
        # Per Reed's docs: key as basic-auth username, empty password
        return httpx.BasicAuth(settings.REED_API_KEY, "")

    def _keywords(self, context: Optional[dict] = None) -> List[str]:
        if context and context.get("queries"):
            return [str(k).strip() for k in context["queries"] if str(k).strip()]
        return [k.strip() for k in settings.REED_KEYWORDS.split(",") if k.strip()]

    def fetch(self, context: Optional[dict] = None) -> List[NormalizedJob]:
        auth = self._auth()
        keywords = self._keywords(context)
        # One search per keyword; with no keywords, a single location-wide
        # search covering ALL professions (profession-neutral default)
        searches: List[Dict[str, Any]] = (
            [{"keywords": kw} for kw in keywords] or [{}]
        )

        location = (context or {}).get("municipality") or (context or {}).get("region") or settings.REED_LOCATION
        distance = settings.REED_DISTANCE_MILES

        jobs: List[NormalizedJob] = []
        seen: set = set()
        per_employer: Dict[str, int] = {}  # bulk posters spam dozens of location variants
        MAX_PER_EMPLOYER = 10
        for extra in searches:
            params: Dict[str, Any] = {"resultsToTake": RESULTS_PER_QUERY, **extra}
            if location:
                params["locationName"] = location
                params["distanceFromLocation"] = distance

            try:
                response = httpx.get(
                    SEARCH_URL,
                    params=params,
                    auth=auth,
                    timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                # PIPE-17: this search unit was never read — mark the
                # fetch partial (see adzuna's note).
                self.fetch_complete = False
                logger.warning("[reed] search failed (%s): %s", extra or "broad", e)
                continue

            for hit in data.get("results", []):
                employer = str(hit.get("employerName") or "")
                if per_employer.get(employer, 0) >= MAX_PER_EMPLOYER:
                    continue
                job = self._normalize(hit, auth)
                if job and job.source_id not in seen:
                    seen.add(job.source_id)
                    per_employer[employer] = per_employer.get(employer, 0) + 1
                    jobs.append(job)

        logger.info("[reed] fetched %d jobs", len(jobs))
        return jobs

    @staticmethod
    def _normalize(hit: Dict[str, Any], auth: httpx.BasicAuth) -> Optional[NormalizedJob]:
        try:
            job_id = str(hit.get("jobId") or hit.get("JobId") or "")
            if not job_id:
                return None

            # Search results usually carry jobUrl; fall back to the details
            # endpoint (same auth) which returns the canonical reed.co.uk url.
            url = hit.get("jobUrl") or hit.get("url")
            if not url:
                url = ReedScraper._details_url(job_id, auth)

            salary = None
            min_s, max_s = hit.get("minimumSalary"), hit.get("maximumSalary")
            if min_s or max_s:
                salary = " - ".join(f"£{_fmt_sal(v)}" for v in (min_s, max_s) if v)

            # Live API returns jobDescription; docs say description — accept both
            description = hit.get("jobDescription") or hit.get("description")
            if description and "<" in description:
                description = strip_html(description)

            return NormalizedJob(
                source="reed",
                source_id=job_id,
                title=hit.get("jobTitle") or "Untitled",
                company=hit.get("employerName"),
                location=hit.get("locationName"),
                url=url or f"https://www.reed.co.uk/jobs?keywords={job_id}",
                description=description,
                salary=salary,
                published_at=_parse_uk_date(hit.get("date")),
            )
        except Exception as e:
            logger.warning("[reed] skipping malformed hit: %s", e)
            return None

    @staticmethod
    def _details_url(job_id: str, auth: httpx.BasicAuth) -> Optional[str]:
        """Fetch /jobs/{id} for the canonical job URL (and richer fields)."""
        try:
            response = httpx.get(
                DETAILS_URL.format(job_id=job_id),
                auth=auth,
                timeout=settings.SCRAPE_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            details = response.json()
            return details.get("url") or details.get("jobUrl")
        except Exception as e:
            logger.debug("[reed] details lookup failed for %s: %s", job_id, e)
            return None
