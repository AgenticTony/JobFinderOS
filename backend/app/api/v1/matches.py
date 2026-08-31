"""Matches API — AI match results, recommendations, and the approval workflow."""

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user, owns_or_404
from app.core.config import settings
from app.core.database import get_db
from app.core.ratelimit import enforce
from app.crud import get_match, list_matches, set_match_decision
from app.models import User
from app.schemas.match import MatchDecision, MatchResponse, MatchWithJobResponse

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_TIERS = {"excellent_match", "good_match", "stretch", "poor_match"}


@router.get("/", response_model=list[MatchWithJobResponse])
async def get_matches(
    tier: str | None = None,
    recommendation: str | None = None,
    min_score: int = Query(0, ge=0, le=100),
    pending_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    if tier and tier not in VALID_TIERS:
        raise HTTPException(status_code=400, detail=f"tier must be one of {sorted(VALID_TIERS)}")
    matches = list_matches(
        db, tier, recommendation, min_score, pending_only, limit, offset, user_id=user.id
    )
    return [MatchWithJobResponse.from_orm_match(m) for m in matches]


@router.get("/{match_id}", response_model=MatchWithJobResponse)
async def get_match_detail(
    match_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    match = get_match(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    owns_or_404(match.user_id, user, "Match")
    return MatchWithJobResponse.from_orm_match(match)


@router.post("/{match_id}/decision", response_model=MatchResponse)
async def decide(
    match_id: int,
    payload: MatchDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    The approval workflow: approve or reject a recommendation.
    Approved jobs become eligible for application.
    """
    if payload.decision not in ("approved", "rejected"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'rejected'")

    match = get_match(db, match_id)
    if not match:
        raise HTTPException(status_code=404, detail="Match not found")
    owns_or_404(match.user_id, user, "Match")

    match = set_match_decision(db, match, payload.decision)
    return MatchResponse.from_orm_match(match)


@router.post("/run")
async def run_matching(
    background: BackgroundTasks,
    # WO-14: both scoring routes bound at the SERVER max — the two old
    # ceilings (100 here, MAX on /pipeline/run) disagreed for the same
    # underlying spend. run_matching now clamps structurally too, so
    # this Query bound is defence-in-depth, not the only defence.
    limit: int | None = Query(None, ge=1, le=settings.MAX_JOBS_PER_MATCH_RUN),
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Run AI matching of unmatched jobs against the active profile.
    Runs in the background; poll GET /matches/ for results.

    WO-14 D3: a user already at their daily scoring cap gets the message
    HERE, synchronously — a background no-op would look like a silent
    empty queue.
    """
    from app.core.database import SessionLocal
    from app.services import matcher_service as svc
    from app.services.cv_service import get_active_profile

    enforce(user.id, 'match_run')
    if not get_active_profile(db, user_id=user.id):
        raise HTTPException(status_code=400, detail="Upload a CV before running matching")

    scored, allowance = svc.daily_scoring_state(db, user_id=user.id)
    if scored >= allowance:
        return {
            "status": "daily_cap_reached",
            "message": svc.daily_cap_message(scored, allowance),
        }

    def _task():
        task_db = SessionLocal()
        try:
            # TENANCY LAYER 1: resolve the caller's profile HERE (on this
            # task's own session) and inject it — run_matching never
            # resolves identity itself.
            task_profile = get_active_profile(task_db, user_id=user.id)
            if not task_profile:
                logger.info("Background matching skipped: no profile for user %s", user.id)
                return
            summary = svc.run_matching(
                task_db,
                limit=limit or settings.MAX_JOBS_PER_MATCH_RUN,
                profile=task_profile,
                max_seconds=settings.MATCH_TIME_BUDGET_SECONDS,
                user_id=user.id,
            )
            logger.info("Background matching finished: %s", summary)
        finally:
            task_db.close()

    background.add_task(_task)
    return {"status": "started", "message": "Matching running in background — refresh matches shortly"}
