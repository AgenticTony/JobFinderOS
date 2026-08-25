"""Applications API — draft preparation, review, and submission."""

import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.crud import get_application, get_job, list_applications
from app.schemas.application import (
    ApplicationResponse,
    DraftResponse,
    DraftSubmitRequest,
    DraftUpdateRequest,
)
from app.services.apply_service import ApplyError, retry_application
from app.services.draft_service import (
    DraftError,
    create_draft_for_job,
    get_draft,
    list_drafts,
    save_draft_edits,
    submit_draft,
)

logger = logging.getLogger(__name__)
router = APIRouter()

VALID_METHODS = {"email", "browser", "manual"}


# ------------------------------------------------------------------
# Drafts — the review stage between match approval and submission
# ------------------------------------------------------------------


@router.post("/draft/{job_id}", response_model=DraftResponse, status_code=201)
async def prepare_draft(job_id: int, force: bool = False, db: Session = Depends(get_db)):
    """
    Tailor the CV + cover letter for an approved job (AI, ~5-20s).
    Returns the draft for user review and editing.
    """
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in ("approved", "matched"):
        raise HTTPException(status_code=400, detail="Approve the match before preparing an application")

    try:
        draft = await run_in_threadpool(create_draft_for_job, db, job, force)
    except DraftError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if draft.status == "failed":
        # 200 with the failed draft so the UI can show the error + retry
        return DraftResponse.from_orm_draft(draft)
    return DraftResponse.from_orm_draft(draft)


@router.get("/drafts", response_model=list[DraftResponse])
async def get_drafts(limit: int = 100, db: Session = Depends(get_db)):
    return [DraftResponse.from_orm_draft(d) for d in list_drafts(db, limit)]


@router.get("/draft/{draft_id}", response_model=DraftResponse)
async def get_draft_detail(draft_id: int, db: Session = Depends(get_db)):
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return DraftResponse.from_orm_draft(draft)


def _pdf_response(blob: bytes, filename: str) -> Response:
    """Build a download response with a sanitized filename."""
    safe = "".join(c if c.isalnum() or c in " ._-()" else "" for c in filename).strip(". ")
    safe = " ".join(safe.split())  # collapse runs of whitespace
    return Response(
        content=blob,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe or "document.pdf"}"'},
    )


def _draft_applicant_name(db: Session) -> str:
    from app.services.cv_service import get_active_profile

    profile = get_active_profile(db)
    return profile.full_name if profile and profile.full_name else "Applicant"


@router.get("/draft/{draft_id}/download/cover-letter")
async def download_cover_letter(draft_id: int, db: Session = Depends(get_db)):
    """Download the (possibly user-edited) cover letter as a PDF — for manual
    upload to portals like LinkedIn that don't allow automated applies."""
    draft = get_draft(db, draft_id)
    if not draft or not draft.cover_letter:
        raise HTTPException(status_code=404, detail="Cover letter not available")
    from app.services import pdf_service

    applicant = _draft_applicant_name(db)
    company = draft.job.company if draft.job else None
    name = f"Cover Letter - {applicant}" + (f" - {company}.pdf" if company else ".pdf")
    blob = await run_in_threadpool(
        pdf_service.cover_letter_pdf, draft.cover_letter, applicant
    )
    return _pdf_response(blob, name)


@router.get("/draft/{draft_id}/download/cv")
async def download_tailored_cv(draft_id: int, db: Session = Depends(get_db)):
    """Download the (possibly user-edited) tailored CV as a PDF — for manual
    upload to portals. The ORIGINAL CV is never modified by tailoring."""
    draft = get_draft(db, draft_id)
    if not draft or not draft.tailored_cv:
        raise HTTPException(status_code=404, detail="Tailored CV not available")
    from app.services import pdf_service

    applicant = _draft_applicant_name(db)
    company = draft.job.company if draft.job else None
    name = f"CV - {applicant} (tailored)" + (f" - {company}.pdf" if company else ".pdf")
    blob = await run_in_threadpool(pdf_service.tailored_cv_pdf, draft.tailored_cv, applicant)
    return _pdf_response(blob, name)


@router.put("/draft/{draft_id}", response_model=DraftResponse)
async def update_draft(draft_id: int, payload: DraftUpdateRequest, db: Session = Depends(get_db)):
    """Save the user's edits to the cover letter / tailored CV."""
    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    try:
        draft = await run_in_threadpool(
            save_draft_edits, db, draft, payload.cover_letter, payload.tailored_cv
        )
    except DraftError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return DraftResponse.from_orm_draft(draft)


@router.post("/draft/{draft_id}/submit", response_model=ApplicationResponse, status_code=201)
async def submit(draft_id: int, payload: DraftSubmitRequest, db: Session = Depends(get_db)):
    """
    Submit the reviewed package.

    - email: sends cover letter + tailored CV PDFs (+ original CV) via Resend
    - browser / manual: queues manual_pending and returns the apply URL —
      the posting opens with the package ready to paste (browser-agent slot)
    """
    if payload.method not in VALID_METHODS:
        raise HTTPException(status_code=400, detail=f"method must be one of {sorted(VALID_METHODS)}")

    draft = get_draft(db, draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")

    try:
        application = await run_in_threadpool(submit_draft, db, draft, payload.method)
    except DraftError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if application.status == "failed":
        logger.warning("Draft submission %s failed: %s", application.id, application.error)
    return application


# ------------------------------------------------------------------
# Application history
# ------------------------------------------------------------------


@router.get("/", response_model=list[ApplicationResponse])
async def get_applications(
    limit: int = 100, offset: int = 0, db: Session = Depends(get_db)
):
    return list_applications(db, limit, offset)


@router.get("/{application_id}", response_model=ApplicationResponse)
async def get_application_detail(application_id: int, db: Session = Depends(get_db)):
    application = get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    return application


@router.post("/{application_id}/retry", response_model=ApplicationResponse)
async def retry(application_id: int, db: Session = Depends(get_db)):
    """Retry a failed email application."""
    application = get_application(db, application_id)
    if not application:
        raise HTTPException(status_code=404, detail="Application not found")
    if application.status != "failed":
        raise HTTPException(status_code=400, detail="Only failed applications can be retried")
    try:
        return await run_in_threadpool(retry_application, db, application)
    except ApplyError as e:
        raise HTTPException(status_code=400, detail=str(e))
