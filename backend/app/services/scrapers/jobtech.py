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
TAXONOMY_URL = "https://taxonomy.api.jobtechdev.se/v1/taxonomy/main/concepts"
PER_QUERY_LIMIT = 100  # API max page size
# WO-06: the official whole-market API is the highest-precision source
# (75% keeper rate vs 13% for the remote aggregators) — walk offset
# pages per query instead of taking only page one.
MAX_PAGES_PER_QUERY = 3

# name -> taxonomy municipality code (JobTech's search API filters by CODE,
# not name — 'municipality: One or more municipality codes, code according
# to the taxonomy' per the official swagger). Fetched once per process.
_MUNICIPALITY_CODES: Optional[Dict[str, str]] = None


def _municipality_codes() -> Dict[str, str]:
    global _MUNICIPALITY_CODES
    if _MUNICIPALITY_CODES is None:
        codes: Dict[str, str] = {}
        try:
            resp = httpx.get(
                TAXONOMY_URL,
                params={"version": 1, "type": "municipality"},
                timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            resp.raise_for_status()
            for c in resp.json():
                label = c.get("taxonomy/preferred-label")
                cid = c.get("taxonomy/id")
                if label and cid:
                    codes[str(label).lower()] = str(cid)
        except Exception as e:  # noqa: BLE001 — a taxonomy miss must not
            # break scraping: without codes we fetch unfiltered and the
            # local location gate still enforces the user's scope.
            logger.warning("[jobtech] taxonomy fetch failed (%s) — "
                           "falling back to unfiltered fetch + local gate", e)
        _MUNICIPALITY_CODES = codes
    return _MUNICIPALITY_CODES


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

        # Fetch-side municipality filtering (official API param): when the
        # user chose municipalities, request ONLY those kommuner instead of
        # fetching all of Sweden and filtering locally. Codes resolve via
        # the taxonomy map; unresolved names fall back to the local gate.
        place_params: List[tuple] = []
        chosen = (context or {}).get("municipalities") or []
        if chosen:
            code_map = _municipality_codes()
            resolved = [code_map[m.lower()] for m in chosen if m.lower() in code_map]
            if resolved:
                place_params = [("municipality", c) for c in resolved]
                logger.info("[jobtech] place-filtered to %d municipality code(s)",
                            len(resolved))
            else:
                logger.warning("[jobtech] no taxonomy codes for %s — "
                               "unfiltered fetch, local gate applies", chosen)

        for query in queries:
            for page in range(MAX_PAGES_PER_QUERY):
                offset = page * PER_QUERY_LIMIT
                try:
                    response = httpx.get(
                        API_URL,
                        params=[("q", query), ("limit", PER_QUERY_LIMIT),
                                ("offset", offset), *place_params],
                        headers=headers,
                        timeout=settings.SCRAPE_TIMEOUT_SECONDS,
                        follow_redirects=True,
                    )
                    response.raise_for_status()
                    data = response.json()
                except Exception as e:
                    logger.warning(
                        "[jobtech] query '%s' page %d failed: %s", query, page, e
                    )
                    break

                hits = data.get("hits", [])
                for hit in hits:
                    job = self._normalize(hit)
                    if job and job.source_id not in seen:
                        seen.add(job.source_id)
                        jobs.append(job)

                if len(hits) < PER_QUERY_LIMIT:
                    break  # short page = end of results for this query

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
