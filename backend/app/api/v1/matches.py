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
    limit: int | None = Query(None, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """
    Run AI matching of unmatched jobs against the active profile.
    Runs in the background; poll GET /matches/ for results.
    """
    from app.core.database import SessionLocal
    from app.services import matcher_service as svc
    from app.services.cv_service import get_active_profile

    def _task():
        task_db = SessionLocal()
        try:
            summary = svc.run_matching(
                task_db,
                limit=limit or settings.MAX_JOBS_PER_MATCH_RUN,
                max_seconds=settings.MATCH_TIME_BUDGET_SECONDS,
                user_id=user.id,
            )
            logger.info("Background matching finished: %s", summary)
        finally:
            task_db.close()

    enforce(user.id, 'match_run')
    if not get_active_profile(db, user_id=user.id):
        raise HTTPException(status_code=400, detail="Upload a CV before running matching")

    background.add_task(_task)
    return {"status": "started", "message": "Matching running in background — refresh matches shortly"}
