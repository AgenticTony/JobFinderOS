"""WO-20 Outcomes API — proposals (propose-then-confirm), nudges, stats,
inbox scan, follow-up drafts, and the manual marker.

Nothing here writes an application outcome except an explicit user
action: a manual marker, or an approved proposal.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user, owns_or_404
from app.core.database import get_db
from app.core.ratelimit import enforce
from app.crud import get_application
from app.models import Application, OutcomeProposal, User
from app.schemas.application import (
    ApplicationResponse,
    DraftResponse,
    OutcomeUpdateRequest,
)
from app.schemas.outcome import OutcomeProposalDecisionRequest, OutcomeProposalResponse
from app.services import composio_service
from app.services.outcome_service import (
    OutcomeError,
    approve_all_pending,
    compute_nudges,
    create_follow_up_draft,
    decide_proposal,
    outcome_stats,
    scan_replies,
    set_outcome,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_owned_proposal(db: Session, proposal_id: int, user: User) -> OutcomeProposal:
    proposal = db.query(OutcomeProposal).filter(OutcomeProposal.id == proposal_id).first()
    if not proposal:
        raise HTTPException(status_code=404, detail="Proposal not found")
    owns_or_404(proposal.user_id, user, "Proposal")
    return proposal


# ------------------------------------------------------------------
# Proposals — propose-then-confirm
# ------------------------------------------------------------------


@router.get("/proposals", response_model=list[OutcomeProposalResponse])
async def get_proposals(
    include_decided: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    query = db.query(OutcomeProposal).filter(OutcomeProposal.user_id == user.id)
    if not include_decided:
        query = query.filter(OutcomeProposal.decision.is_(None))
    proposals = query.order_by(OutcomeProposal.created_at.asc()).all()
    # job_id denormalized for the review card (one query, not per-row)
    app_ids = {p.application_id for p in proposals}
    job_by_app = dict(
        db.query(Application.id, Application.job_id)
        .filter(Application.id.in_(app_ids)).all()
    ) if app_ids else {}
    return [
        OutcomeProposalResponse.from_orm_proposal(p, job_by_app.get(p.application_id))
        for p in proposals
    ]


@router.post("/proposals/approve-all")
async def approve_all(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Confirm the batch — every pending proposal applied (each still
    routes the same approval path as a single confirm)."""
    enforce(user.id, "outcome_decide")
    count = await run_in_threadpool(approve_all_pending, db, user_id=user.id)
    return {"approved": count}


@router.post("/proposals/{proposal_id}/decide", response_model=ApplicationResponse)
async def decide(
    proposal_id: int,
    payload: OutcomeProposalDecisionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """Approve (apply to the application) or dismiss one proposal."""
    enforce(user.id, "outcome_decide")
    proposal = _get_owned_proposal(db, proposal_id, user)
    if payload.decision not in ("approved", "dismissed"):
        raise HTTPException(status_code=400, detail="decision must be 'approved' or 'dismissed'")
    try:
        application = await run_in_threadpool(
            decide_proposal, db, proposal, payload.decision
        )
    except OutcomeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApplicationResponse.from_orm_application(application)


# ------------------------------------------------------------------
# Nudges + stats
# ------------------------------------------------------------------


@router.get("/nudges")
async def nudges(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    return await run_in_threadpool(compute_nudges, db, user_id=user.id)


@router.get("/stats")
async def stats(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    return await run_in_threadpool(outcome_stats, db, user_id=user.id)


# ------------------------------------------------------------------
# Inbox scan (Gmail via Composio — the read side of the connection)
# ------------------------------------------------------------------


@router.post("/scan")
async def scan(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """One incremental pass over the caller's connected Gmail. Files
    proposals only — never writes applications. Also runs the 60-day
    quiet sweep so a manual trigger covers everything the daily worker
    would."""
    from app.services.outcome_service import propose_no_response_sweep

    enforce(user.id, "outcome_scan")
    if not composio_service.is_configured():
        raise HTTPException(status_code=503, detail="Integrations are not configured on this deployment")
    connections = await composio_service.list_connections(str(user.id))
    if connections is None:
        # Unknown ≠ not connected (2026-10-30 audit, D2): telling a
        # connected user to "connect Gmail" during a provider hiccup is
        # a false claim — say the status is unavailable instead.
        raise HTTPException(
            status_code=503,
            detail="Integration status is temporarily unavailable — try again shortly",
        )
    if not any(c.get("app_name") == "gmail" for c in connections):
        raise HTTPException(
            status_code=400, detail="Connect your Gmail in Settings first"
        )

    from app.services.gmail_scan import gmail_fetch_messages

    try:
        # CLAUDE.md lesson #1: scan_replies interleaves up to 50 Composio
        # round-trips with synchronous DB flushes/commits — awaiting it
        # directly runs every DB call on the event loop and stalls all
        # other requests for the scan's duration. asyncio.run on a worker
        # thread gives the coroutine its own loop OFF the main one (review
        # round 1, finding 10).
        import asyncio

        summary = await run_in_threadpool(
            asyncio.run, scan_replies(db, user.id, gmail_fetch_messages)
        )
    except composio_service.ComposioError as e:
        raise HTTPException(status_code=502, detail=str(e))
    swept = await run_in_threadpool(propose_no_response_sweep, db, user_id=user.id)
    summary["sweep_proposals"] = swept
    return summary


# ------------------------------------------------------------------
# Manual marker + follow-up draft (application-scoped)
# ------------------------------------------------------------------


@router.post("/{application_id}/outcome", response_model=ApplicationResponse)
async def mark_outcome(
    application_id: int,
    payload: OutcomeUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """The user's own word about what happened. Manual outcomes are the
    highest authority — a later detected proposal can never overwrite
    one (decide_proposal enforces it)."""
    enforce(user.id, "outcome_update")
    application = get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    owns_or_404(application.user_id, user, "Application")
    try:
        application = await run_in_threadpool(
            set_outcome,
            db,
            application,
            payload.outcome_status,
            "manual",
            note=payload.note,
        )
    except OutcomeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ApplicationResponse.from_orm_application(application)


@router.post("/{application_id}/follow-up", response_model=DraftResponse, status_code=201)
async def follow_up(
    application_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_authenticated_user),
):
    """Draft a short follow-up (max 2 per application) in the user's
    voice, grounded only in the original application. It is a NORMAL
    draft: the same review-edit-approve gates, the same send paths."""
    enforce(user.id, "followup_create")
    application = get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    owns_or_404(application.user_id, user, "Application")

    # TENANCY LAYER 1: the follow-up speaks in the caller's voice —
    # resolve the profile here, inject; the service never looks one up.
    from app.services.cv_service import get_active_profile

    profile = get_active_profile(db, user_id=user.id)
    if not profile:
        raise HTTPException(status_code=400, detail="No CV on file for this account")
    try:
        draft = await run_in_threadpool(
            create_follow_up_draft, db, application, profile
        )
    except OutcomeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return DraftResponse.from_orm_draft(draft)
