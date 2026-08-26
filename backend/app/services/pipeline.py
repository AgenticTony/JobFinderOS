"""
Pipeline service — orchestrates the full JobFinderOS loop:

    scrape sources -> per-user location filter -> AI match vs profile -> recommend

This is the job-seeker inversion of TalentHive's demo screening orchestration.
When the user has completed onboarding, their country picks the source pack,
their CV-derived queries drive the targeted boards, and their region/city
filters what gets stored at all.
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.dedupe import dedupe_key_for
from app.models import JobPosting, MatchResult, Profile, ScrapeRun
from app.schemas.common import dump_json_list, parse_json_list
from app.services import matcher_service, source_packs
from app.services.language_filter import passes_language_filter
from app.services.scrapers import SCRAPER_REGISTRY, NormalizedJob

logger = logging.getLogger(__name__)


def build_scrape_context(db: Session) -> Optional[Dict]:
    """Per-user scrape settings from the active profile's onboarding."""
    profile = (
        db.query(Profile).filter(Profile.is_active == 1, Profile.country.isnot(None)).first()
    )
    if not profile:
        return None
    return {
        "country": (profile.country or "").upper(),
        "region": profile.region,
        "municipality": profile.municipality,
        "remote_only": bool(profile.remote_only),
        "include_remote": bool(profile.include_remote),
        "queries": parse_json_list(profile.search_queries),
        "languages": parse_json_list(profile.languages) or [],
    }


def passes_location_filter(job: NormalizedJob, ctx: Dict) -> bool:
    """
    Universal location gate — applied to every source identically.

    - Jobs located in the user's municipality/region always pass
      (local on-site, local remote, hybrid)
    - Remote jobs and location-less jobs only pass when the user opted
      into remote work in onboarding (include_remote) — otherwise the
      search is strictly local
    - remote_only users additionally drop non-remote jobs
    """
    if ctx.get("remote_only") and not job.remote:
        return False

    terms = [t.lower() for t in (ctx.get("municipality"), ctx.get("region")) if t]
    if terms and job.location and any(term in job.location.lower() for term in terms):
        return True

    # Outside the area (or no location text): only for remote-opted users
    return bool(ctx.get("include_remote")) and bool(job.remote)


def scrape_source(db: Session, source_name: str, ctx: Optional[Dict] = None) -> ScrapeRun:
    """Run one scraper, upsert new jobs, record a ScrapeRun audit row."""
    run = ScrapeRun(source=source_name, status="running")
    db.add(run)
    db.commit()

    scraper_cls = SCRAPER_REGISTRY.get(source_name)
    if scraper_cls is None:
        run.status = "failed"
        run.error = f"Unknown source: {source_name}"
        run.finished_at = datetime.utcnow()
        db.commit()
        return run

    if not scraper_cls.is_configured(ctx):
        run.status = "skipped"
        run.error = f"{source_name} not configured (see backend/.env.example)"
        run.finished_at = datetime.utcnow()
        db.commit()
        return run

    try:
        jobs: List[NormalizedJob] = scraper_cls().fetch(ctx)
        run.jobs_found = len(jobs)

        # Universal location gate — out-of-area jobs are never stored,
        # so they never consume matching budget
        if ctx:
            before = len(jobs)
            jobs = [nj for nj in jobs if passes_location_filter(nj, ctx)]
            if before != len(jobs):
                logger.info("[%s] location filter: %d -> %d jobs", source_name, before, len(jobs))

            # Freshness gate — postings older than MAX_POSTING_AGE_DAYS
            # are almost certainly closed; never store them
            max_age = timedelta(days=settings.MAX_POSTING_AGE_DAYS)
            fresh = len(jobs)
            jobs = [
                nj
                for nj in jobs
                if nj.published_at is None or nj.published_at >= datetime.utcnow() - max_age
            ]
            if fresh != len(jobs):
                logger.info("[%s] freshness gate: %d -> %d jobs", source_name, fresh, len(jobs))

            # Language gate — postings in languages the user doesn't speak
            # are dropped before storing (English always passes)
            before = len(jobs)
            jobs = [
                nj
                for nj in jobs
                if passes_language_filter(nj.title, nj.description, ctx.get("languages", []))
            ]
            if before != len(jobs):
                logger.info(
                    "[%s] language filter: %d -> %d jobs", source_name, before, len(jobs)
                )

        new_count = 0
        for nj in jobs:
            if _job_exists(db, nj):
                continue
            posting = JobPosting(
                source=nj.source,
                source_id=nj.source_id,
                dedupe_key=dedupe_key_for(nj.title, nj.company, nj.location),
                title=nj.title[:500],
                company=nj.company,
                location=nj.location,
                remote=1 if nj.remote else 0,
                url=nj.url[:1000],
                description=nj.description,
                employment_type=nj.employment_type,
                salary=nj.salary,
                tags=dump_json_list(nj.tags),
                category=nj.category,
                application_email=nj.application_email,
                application_url=nj.application_url,
                published_at=nj.published_at,
            )
            db.add(posting)
            db.flush()  # make the row visible so same-run duplicates are caught
            new_count += 1

        db.commit()
        run.jobs_new = new_count
        run.status = "completed"
        logger.info("[%s] %d found, %d new", source_name, len(jobs), new_count)
    except Exception as e:
        db.rollback()
        run.status = "failed"
        run.error = str(e)[:2000]
        logger.error("[%s] scrape failed: %s", source_name, e)
    finally:
        run.finished_at = datetime.utcnow()
        db.commit()

    return run


def _job_exists(db: Session, nj: NormalizedJob) -> bool:
    """Dedupe by (source, source_id) then by URL."""
    if nj.source_id:
        exists = (
            db.query(JobPosting.id)
            .filter(JobPosting.source == nj.source, JobPosting.source_id == nj.source_id)
            .first()
        )
        if exists:
            return True
    if nj.url:
        exists = db.query(JobPosting.id).filter(JobPosting.url == nj.url[:1000]).first()
        if exists:
            return True
    # Cross-board duplicate: same normalized title+company already stored
    key = dedupe_key_for(nj.title, nj.company, nj.location)
    exists = db.query(JobPosting.id).filter(JobPosting.dedupe_key == key).first()
    return bool(exists)
# NOTE: autoflush is off, so same-run adds are invisible to these queries.
# We flush each posting immediately to make same-run dedup work.


def run_pipeline(
    sources: Optional[List[str]] = None,
    match: bool = True,
    max_matches: Optional[int] = None,
) -> Dict:
    """
    Run the full pipeline (used by the API and the scheduler).

    Returns a summary dict (also logged by the scheduler).
    """
    db = SessionLocal()
    try:
        ctx = build_scrape_context(db)
        # Per-user pack when onboarded; explicit request or global allow-list otherwise
        if sources:
            requested = sources
        elif ctx:
            requested = [s for s in source_packs.pack_for_country(ctx["country"]) if s in SCRAPER_REGISTRY]
            # Worldwide remote boards are pointless for a strictly-local
            # user — don't even spend the requests
            if not ctx.get("include_remote"):
                requested = [s for s in requested if s not in source_packs.SHARED_REMOTE_SOURCES]
                if not requested:
                    logger.info("Strictly-local user — remote boards skipped")
        else:
            requested = settings.get_scrape_sources()
        scrape_summaries = []
        for source in requested:
            run = scrape_source(db, source, ctx)
            scrape_summaries.append(
                {
                    "source": run.source,
                    "status": run.status,
                    "jobs_found": run.jobs_found,
                    "jobs_new": run.jobs_new,
                    "error": run.error,
                }
            )

        _maintenance_sweeps(db)

        match_summary = None
        if match:
            try:
                match_summary = matcher_service.run_matching(
                    db, limit=max_matches, max_seconds=settings.MATCH_TIME_BUDGET_SECONDS
                )
            except Exception as e:  # noqa: BLE001 — report in summary, never 500 the endpoint
                db.rollback()
                match_summary = {
                    "status": "failed",
                    "jobs_considered": 0,
                    "matches_created": 0,
                    "error": f"{type(e).__name__}: {e}",
                }

        # Top recommendations of this run for immediate display
        top_matches = (
            db.query(MatchResult)
            .join(JobPosting, MatchResult.job_id == JobPosting.id)
            .filter(MatchResult.decision.is_(None), JobPosting.status == "matched")
            .order_by(MatchResult.score.desc())
            .limit(10)
            .all()
        )

        return {
            "scrape": scrape_summaries,
            "match": match_summary,
            "top_matches": [m.id for m in top_matches],
        }
    finally:
        db.close()


def _maintenance_sweeps(db: Session) -> None:
    """Queue hygiene: expire stale unmatched postings and auto-pass stale
    pending matches. Runs inside every pipeline run."""
    now = datetime.utcnow()

    from sqlalchemy import or_

    stale_cutoff = now - timedelta(days=settings.MAX_POSTING_AGE_DAYS)
    # Postings with a publication date expire by it; date-less postings
    # (NULL published_at never satisfies `<`) expire by when we scraped them.
    stale_new = (
        db.query(JobPosting)
        .filter(
            JobPosting.status == "new",
            or_(
                JobPosting.published_at < stale_cutoff,
                (JobPosting.published_at.is_(None)) & (JobPosting.scraped_at < stale_cutoff),
            ),
        )
        .all()
    )
    for job in stale_new:
        job.status = "dismissed"
    if stale_new:
        logger.info("Sweep: dismissed %d stale unmatched postings", len(stale_new))

    old_cutoff = now - timedelta(days=settings.MATCH_STALE_DAYS)
    old_pending = (
        db.query(MatchResult)
        .filter(MatchResult.decision.is_(None))
        .filter(MatchResult.created_at < old_cutoff)
        .all()
    )
    for m in old_pending:
        m.decision = "rejected"
        m.decided_at = now
        job = db.get(JobPosting, m.job_id)
        if job and job.status == "matched":
            job.status = "rejected"
    if old_pending:
        logger.info("Sweep: auto-passed %d pending matches older than %dd", len(old_pending), settings.MATCH_STALE_DAYS)
    db.commit()
