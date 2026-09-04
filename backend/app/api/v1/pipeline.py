"""Pipeline API — the scrape -> match -> recommend loop."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.config import settings
from app.core.database import SessionLocal, get_db
from app.core.ratelimit import enforce
from app.crud import get_stats, list_scrape_runs
from app.models import MatchResult, User
from app.schemas.match import MatchWithJobResponse
from app.schemas.pipeline import PipelineRunRequest, PipelineRunResponse, ScrapeSummary
from app.services.pipeline import run_pipeline
from app.services.scrapers import SCRAPER_REGISTRY
from app.services.worker import claim_hunt, release_hunt, renew_hunt

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/run", response_model=PipelineRunResponse)
async def run(
    payload: PipelineRunRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Run the full pipeline: scrape enabled sources, store new jobs,
    AI-match them against the active profile, return top recommendations.

    This is the main button of JobFinderOS.
    """
    sources = payload.sources  # None = per-user country pack (or global allow-list)
    unknown = [s for s in sources if s not in SCRAPER_REGISTRY] if sources else []
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown sources {unknown}. Available: {sorted(SCRAPER_REGISTRY)}",
        )

    enforce(user.id, 'hunt')

    # PIPE-14a: a manual hunt runs the SAME shared-pool scrape unit as
    # the cron worker (sources, watermarks, job_postings), so it claims
    # the SAME DB hunt lock the worker claims (claim_hunt: one atomic
    # conditional UPDATE, portable, TTL-stealable — no new machinery).
    # The lock is GLOBAL, deliberately: the scrape unit it guards is
    # global, and this one claim closes both races that used to
    # double-scrape/double-AI-score the pool (manual-vs-cron and
    # manual-vs-manual — the DATA-5 watermark race is exactly two
    # concurrent runs). A busy press gets an honest 409 mirroring the
    # worker's "lock held — skip" instead of silently double-spending.
    # Trade-off accepted: a manual hunt holding the claim makes an
    # overlapping cron cycle skip (the worker's existing lock_held
    # behavior) — hunts are bounded by the same worst-case budget as the
    # claim TTL, and a crashed holder self-heals after the TTL.
    lock_db = SessionLocal()
    try:
        claim_token = claim_hunt(lock_db)
    finally:
        lock_db.close()
    if not claim_token:
        raise HTTPException(
            status_code=409,
            detail="A hunt is already running (scheduled or manual). "
                   "It finishes within a few minutes — try again after that.",
        )

    def _renew_claim():
        # PIPE-18b: uncapped matching can outlive the claim TTL —
        # renew from inside the evaluation loop (own session: the
        # loop may run in the threadpool on a different session).
        renew_db = SessionLocal()
        try:
            return renew_hunt(renew_db, claim_token)
        finally:
            renew_db.close()

    try:
        summary = await run_in_threadpool(
            run_pipeline,
            sources=sources,
            match=payload.match,
            max_matches=payload.max_matches,
            backfill=payload.backfill,
            heartbeat=_renew_claim,
            user_id=user.id,
        )
    finally:
        # ALWAYS released BY ITS OWNER (the worker's rule; PIPE-18 —
        # release is keyed on the claim's owner token, so a TTL-stolen
        # claim is never freed by its overrunner): a leaked claim is a
        # silent hunt outage for a full claim TTL.
        release_db = SessionLocal()
        try:
            release_hunt(release_db, claim_token)
        finally:
            release_db.close()

    # Re-read top matches with jobs joined for the response.
    # Defense in depth (P0-1): the id list comes from the service's
    # user-scoped query, but this re-fetch scopes by user_id TOO — an
    # unscoped id-in re-fetch would re-open the cross-user leak if the
    # upstream filter ever regresses.
    db.expire_all()
    top = (
        db.query(MatchResult)
        .filter(
            MatchResult.id.in_(summary.get("top_matches") or [0]),
            MatchResult.user_id == user.id,
        )
        .order_by(MatchResult.score.desc())
        .all()
        if summary.get("top_matches")
        else []
    )

    return PipelineRunResponse(
        scrape=[ScrapeSummary(**s) for s in summary["scrape"]],
        match=summary.get("match"),
        top_matches=[MatchWithJobResponse.from_orm_match(m) for m in top],
    )


@router.get("/status")
async def status(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Dashboard readiness: source list, stats, recent scrape runs, live
    match flag (the CALLER's — AI-14: a global flag told every user
    "matching in progress" whenever ANY user matched)."""
    from app.services.matcher_service import is_matching_running
    from app.services.scheduler import get_next_run_time, next_run_from_fixed_times

    next_run = get_next_run_time()
    # In production the in-process scheduler is off (ENABLE_SCHEDULER=false
    # in render.yaml) and hunts run via the EXTERNAL cron. HUNT_TIMES_UTC
    # describes that cron so the dashboard counts down to something true
    # instead of reporting "automatic hunts off".
    hunts_automated = settings.ENABLE_SCHEDULER
    if next_run is None and settings.HUNT_TIMES_UTC:
        next_run = next_run_from_fixed_times(settings.HUNT_TIMES_UTC)
        hunts_automated = next_run is not None
    runs = list_scrape_runs(db, limit=12)
    return {
        "sources_available": sorted(SCRAPER_REGISTRY.keys()),
        "sources_enabled": settings.get_scrape_sources(),
        "scheduler_enabled": hunts_automated,
        "scrape_interval_minutes": settings.SCRAPE_INTERVAL_MINUTES,
        "next_run_at": next_run.isoformat() if next_run else None,
        "matching_running": is_matching_running(user_id=user.id),
        "stats": get_stats(db, user_id=user.id),
        "recent_runs": [
            {
                "source": r.source,
                "status": r.status,
                "jobs_found": r.jobs_found,
                "jobs_new": r.jobs_new,
                "error": r.error,
                "started_at": r.started_at,
            }
            for r in runs
        ],
    }
