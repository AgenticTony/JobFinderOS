"""Pipeline API — the scrape -> match -> recommend loop."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.config import settings
from app.core.database import get_db
from app.core.ratelimit import enforce
from app.crud import get_stats, list_scrape_runs
from app.models import MatchResult, User
from app.schemas.match import MatchWithJobResponse
from app.schemas.pipeline import PipelineRunRequest, PipelineRunResponse, ScrapeSummary
from app.services.pipeline import run_pipeline
from app.services.scrapers import SCRAPER_REGISTRY

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
    summary = await run_in_threadpool(
        run_pipeline,
        sources=sources,
        match=payload.match,
        max_matches=payload.max_matches,
        backfill=payload.backfill,
        user_id=user.id,
    )

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
    """Dashboard readiness: source list, stats, recent scrape runs, live match flag."""
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
        "matching_running": is_matching_running(),
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
