"""
Careerjet scraper — global aggregator with a documented search API.

Docs: https://search.api.careerjet.net/v4/query
Auth: basic auth — API key as username, empty password (same pattern as Reed).
Serves BOTH packs via locale_code (en_GB / sv_SE) — it aggregates listings from
many boards, including ones closed to us directly.

Gated behind CAREERJET_API_KEY; activates the moment the key lands in .env.
"""

import logging
import re
import socket
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional

import httpx

from app.core.config import settings
from app.services.scrapers.base import BaseScraper, NormalizedJob

logger = logging.getLogger(__name__)

SEARCH_URL = "https://search.api.careerjet.net/v4/query"
PAGE_SIZE = 100  # API max
FRAGMENT_SIZE = 2000  # bigger excerpts -> better AI matching

# Onboarding country -> Careerjet locale
LOCALES = {"GB": "en_GB", "SE": "sv_SE"}

USER_AGENT = "JobFinderOS/0.1 (job-search-automation)"


_cached_public_ip: Optional[str] = None


def _outbound_ip() -> str:
    """The API requires user_ip and validates it against the key's allowlist —
    must be the PUBLIC IP. Looked up once via an echo service, then cached."""
    global _cached_public_ip
    if _cached_public_ip:
        return _cached_public_ip
    try:
        response = httpx.get("https://api.ipify.org", timeout=10)
        if response.status_code == 200 and response.text.strip():
            _cached_public_ip = response.text.strip()
            return _cached_public_ip
    except Exception:
        pass
    try:  # offline fallback: local LAN address (will fail the allowlist, but survives)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _parse_date(raw: Optional[str]) -> Optional[datetime]:
    """Careerjet dates: 'Wed,15 Nov 2025 19:13:43 GMT' (RFC2822, missing space)."""
    if not raw:
        return None
    try:
        normalized = re.sub(r"([A-Za-z]{3}),(\d)", r"\1, \2", raw)
        return parsedate_to_datetime(normalized).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


class CareerjetScraper(BaseScraper):
    source = "careerjet"

    @classmethod
    def is_configured(cls, context: Optional[dict] = None) -> bool:
        return bool(settings.CAREERJET_API_KEY)

    def fetch(self, context: Optional[dict] = None) -> List[NormalizedJob]:
        auth = httpx.BasicAuth(settings.CAREERJET_API_KEY, "")
        country = ((context or {}).get("country") or "GB").upper()
        locale = LOCALES.get(country, "en_GB")

        queries = [str(q).strip() for q in ((context or {}).get("queries") or []) if str(q).strip()]
        searches: List[Optional[str]] = queries or [None]
        location = (context or {}).get("municipality") or (context or {}).get("region")

        jobs: List[NormalizedJob] = []
        seen: set = set()
        for keywords in searches:
            params = {
                "locale_code": locale,
                "page": 1,
                "page_size": PAGE_SIZE,
                "fragment_size": FRAGMENT_SIZE,
                "sort": "date",  # freshest first — dedup keeps old ones from prior runs
                "user_ip": _outbound_ip(),  # required by the API
                "user_agent": USER_AGENT,  # required by the API
            }
            if keywords:
                params["keywords"] = keywords
            if location:
                params["location"] = location

            try:
                response = httpx.get(
                    SEARCH_URL,
                    params=params,
                    auth=auth,
                    timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                    # Careerjet validates the Referer against the website
                    # declared in the partner portal
                    headers={"Referer": settings.CAREERJET_REFERER},
                )
                response.raise_for_status()
                data = response.json()
            except Exception as e:
                logger.warning("[careerjet] search %r failed: %s", keywords, e)
                continue

            # Location mode: ambiguous/unknown location -> retry with first match
            if data.get("type") == "LOCATIONS":
                choices = data.get("locations") or []
                if choices and location:
                    logger.info("[careerjet] location %r ambiguous, retrying with %r", location, choices[0])
                    params["location"] = choices[0]
                    try:
                        response = httpx.get(
                            SEARCH_URL,
                            params=params,
                            auth=auth,
                            timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                            headers={"Referer": settings.CAREERJET_REFERER},
                        )
                        response.raise_for_status()
                        data = response.json()
                    except Exception as e:
                        logger.warning("[careerjet] location retry failed: %s", e)
                        continue
                else:
                    logger.info("[careerjet] no matching location for %r", location)
                    continue

            for hit in data.get("jobs", []):
                job = self._normalize(hit)
                if job and job.source_id not in seen:
                    seen.add(job.source_id)
                    jobs.append(job)

        logger.info("[careerjet] fetched %d jobs (locale=%s)", len(jobs), locale)
        return jobs

    @staticmethod
    def _normalize(hit: dict) -> Optional[NormalizedJob]:
        try:
            url = hit.get("url") or ""
            if not url:
                return None
            # jobviewtrack links vary per impression; derive a stable id from
            # the url (md5 — Python's hash() is per-process randomized)
            import hashlib

            source_id = hashlib.md5(url.encode("utf-8")).hexdigest()[:16]

            return NormalizedJob(
                source="careerjet",
                source_id=source_id,
                title=hit.get("title") or "Untitled",
                company=hit.get("company"),
                location=hit.get("locations") or None,
                url=url,
                description=hit.get("description"),
                salary=hit.get("salary") or None,  # preformatted: "GBP 30000 - 33000 per year"
                published_at=_parse_date(hit.get("date")),
            )
        except Exception as e:
            logger.warning("[careerjet] skipping malformed hit: %s", e)
            return None
