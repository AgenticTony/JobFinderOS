"""
Adzuna scraper — official jobs-search API (UK flagship alongside Reed).

API: https://api.adzuna.com/v1/api/jobs/{country}/search/{page}
Docs: https://developer.adzuna.com — free app_id + app_key registration.
Blind-built per documented response shape; activates when keys are set.

Response (documented): {results: [{id, created, title, description,
redirect_url, company: {display_name}, location: {display_name, area: [...]},
salary_min, salary_max, contract_time, contract_type, category: {label}}]}
"""

import logging
import time
from datetime import datetime
from typing import List, Optional

import httpx

from app.core.config import settings
from app.services.scrapers.base import BaseScraper, NormalizedJob, strip_html

logger = logging.getLogger(__name__)

BASE_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
RESULTS_PER_PAGE = 50
PAGES = 2  # 2 x 50 = up to 100 per run — free tier is 25 hits/min, 250/day
PAGE_DELAY_SECONDS = 4  # pace requests; the free tier also has short-window buckets
RETRIES = 2
RETRY_DELAY_SECONDS = 6  # 503s from Adzuna are rate-limit responses — back off

# Adzuna country codes we support (no 'se' — JobTech covers Sweden)
COUNTRY_CODES = {"GB": "gb", "US": "us", "DE": "de", "FR": "fr", "NL": "nl"}


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.split("+")[0].split(".")[0])
    except ValueError:
        return None


class AdzunaScraper(BaseScraper):
    source = "adzuna"

    @classmethod
    def is_configured(cls, context: Optional[dict] = None) -> bool:
        return bool(settings.ADZUNA_APP_ID and settings.ADZUNA_APP_KEY)

    def fetch(self, context: Optional[dict] = None) -> List[NormalizedJob]:
        country = ((context or {}).get("country") or "GB").upper()
        cc = COUNTRY_CODES.get(country, "gb")

        queries = [str(q).strip() for q in ((context or {}).get("queries") or []) if str(q).strip()]
        # One search per query (OR-combining them trips rate buckets);
        # no queries = one broad location-wide search, all professions
        searches: List[Optional[str]] = queries or [None]
        where = (context or {}).get("municipality") or (context or {}).get("region")

        jobs: List[NormalizedJob] = []
        seen: set = set()
        first_request = True
        for what in searches:
            for page in range(1, PAGES + 1):
                if not first_request:
                    time.sleep(PAGE_DELAY_SECONDS)  # stay under the rate buckets
                first_request = False

                params = {
                    "app_id": settings.ADZUNA_APP_ID,
                    "app_key": settings.ADZUNA_APP_KEY,
                    "results_per_page": RESULTS_PER_PAGE,
                    "content-type": "application/json",
                }
                if what:
                    params["what"] = what
                if where:
                    params["where"] = where
                    params["distance"] = 20
                try:
                    response = self._get_with_retry(BASE_URL.format(country=cc, page=page), params)
                    data = response.json()
                except Exception as e:
                    logger.warning("[adzuna] search %r page %d failed: %s", what, page, e)
                    continue

                for hit in data.get("results", []):
                    job = self._normalize(hit)
                    if job and job.source_id not in seen:
                        seen.add(job.source_id)
                        jobs.append(job)

        logger.info("[adzuna] fetched %d jobs", len(jobs))
        return jobs

    def _get_with_retry(self, url: str, params: dict) -> "httpx.Response":
        """GET with backoff — Adzuna answers 503 when a rate bucket is hit."""
        last_error: Optional[Exception] = None
        for attempt in range(1, RETRIES + 1):
            response = httpx.get(url, params=params, timeout=settings.SCRAPE_TIMEOUT_SECONDS)
            if response.status_code == 200:
                return response
            if response.status_code in (429, 503):
                logger.info(
                    "[adzuna] rate limited (HTTP %s), backing off %ss (attempt %d/%d)",
                    response.status_code, RETRY_DELAY_SECONDS, attempt, RETRIES,
                )
                time.sleep(RETRY_DELAY_SECONDS)
                last_error = Exception(f"rate limited: HTTP {response.status_code}")
                continue
            response.raise_for_status()
        raise last_error or Exception("adzuna request failed")

    @staticmethod
    def _normalize(hit: dict) -> Optional[NormalizedJob]:
        try:
            job_id = str(hit.get("id") or "")
            if not job_id:
                return None

            company = (hit.get("company") or {}).get("display_name")
            location = (hit.get("location") or {})
            location_text = location.get("display_name") or ", ".join(
                location.get("area") or []
            )
            salary = None
            min_s, max_s = hit.get("salary_min"), hit.get("salary_max")
            if min_s or max_s:
                salary = " - ".join(f"£{v:,.0f}" for v in (min_s, max_s) if v)

            category = (hit.get("category") or {}).get("label")
            description = hit.get("description")
            if description and "<" in description:
                description = strip_html(description)

            return NormalizedJob(
                source="adzuna",
                source_id=job_id,
                title=hit.get("title") or "Untitled",
                company=company,
                location=location_text or None,
                url=hit.get("redirect_url") or "",
                description=description,
                employment_type=hit.get("contract_type") or hit.get("contract_time"),
                salary=salary,
                tags=[category] if category else [],
                category=category,
                published_at=_parse_dt(hit.get("created")),
            )
        except Exception as e:
            logger.warning("[adzuna] skipping malformed hit: %s", e)
            return None
