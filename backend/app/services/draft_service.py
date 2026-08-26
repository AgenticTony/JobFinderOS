"""
Draft service — the application-preparation stage between match approval
and submission.

Flow: user approves a match -> create_draft_for_job() runs AI tailoring ->
user reviews/edits the package in the UI -> submit_draft() sends it
(email with tailored PDFs, or browser/manual queue).

INVARIANT — THE ORIGINAL CV IS NEVER TOUCHED:
profile.cv_text and the stored PDF are read-only inputs here. Tailoring
always writes to THIS draft's cover_letter / tailored_cv columns, one row
per job. The original CV stays the permanent reference for every future
match and every future draft, and is attached unmodified alongside the
tailored documents when sending.
"""

import logging
from typing import List, Optional

from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.models import Application, ApplicationDraft, JobPosting, MatchResult, Profile
from app.services import pdf_service
from app.services.ai_service import ai_service_available, get_ai_service
from app.services.cv_service import build_profile_context
from app.services.matcher_service import _job_text

logger = logging.getLogger(__name__)

DRAFT_DIR = "uploads/drafts"


class DraftError(Exception):
    """Raised when a draft cannot be created or submitted."""


def get_draft(db: Session, draft_id: int) -> Optional[ApplicationDraft]:
    return db.query(ApplicationDraft).filter(ApplicationDraft.id == draft_id).first()


def list_drafts(db: Session, limit: int = 100, *, user_id) -> List[ApplicationDraft]:
    return (
        db.query(ApplicationDraft)
        .filter(ApplicationDraft.user_id == user_id)
        .order_by(ApplicationDraft.updated_at.desc())
        .limit(limit)
        .all()
    )


def create_draft_for_job(
    db: Session,
    job: JobPosting,
    *,
    profile: Profile,
    force: bool = False,
    user_id,
) -> ApplicationDraft:
    """
    Generate (or regenerate) the tailored application package for an approved job.

    TENANCY LAYER 1: the profile arrives as a required parameter — this
    function never resolves identity itself. The three cross-tenant P0
    leaks all came from services fetching "the" profile internally; the
    route resolves the caller's profile and hands it in.

    The AI tailoring call is synchronous here — the API endpoint wraps this in
    a threadpool. Typical latency ~5-20s on glm-4.6 with thinking disabled.
    """
    existing = (
        db.query(ApplicationDraft)
        .filter(
            ApplicationDraft.job_id == job.id,
            ApplicationDraft.user_id == user_id,
            ApplicationDraft.status != "submitted",
        )
        .first()
    )
    if existing and existing.status == "ready" and not force:
        return existing  # already prepared — user should review, not regenerate

    if not profile or not profile.cv_text:
        raise DraftError("Upload your CV before preparing applications")

    if not ai_service_available():
        raise DraftError("GLM_API_KEY not configured — cannot tailor applications")

    match: Optional[MatchResult] = (
        db.query(MatchResult)
        .filter(MatchResult.job_id == job.id, MatchResult.user_id == user_id)
        .first()
    )

    # Reuse the existing row when regenerating a failed draft
    draft = existing or ApplicationDraft(
        user_id=user_id, job_id=job.id, match_id=match.id if match else None
    )
    draft.status = "drafting"
    draft.error = None
    db.add(draft)
    db.commit()

    try:
        result = get_ai_service().tailor_application(
            profile_context=build_profile_context(profile),
            cv_text=profile.cv_text,
            job_description=_job_text(job),
        )
        draft.cover_letter = result["cover_letter"]
        draft.tailored_cv = result["tailored_cv"]
        from app.schemas.common import dump_json_list

        draft.changes_summary = dump_json_list(result["changes_summary"])
        draft.status = "ready"
        db.add(draft)
        db.commit()
        db.refresh(draft)
        logger.info("Draft %s prepared for job %s", draft.id, job.id)
        return draft
    except Exception as e:  # noqa: BLE001 — surface the failure on the draft row
        db.rollback()
        draft.status = "failed"
        draft.error = f"Tailoring failed: {type(e).__name__}: {e}"
        db.add(draft)
        db.commit()
        db.refresh(draft)
        logger.error("Draft tailoring failed for job %s: %s", job.id, e)
        return draft


def save_draft_edits(
    db: Session,
    draft: ApplicationDraft,
    cover_letter: Optional[str] = None,
    tailored_cv: Optional[str] = None,
) -> ApplicationDraft:
    """Persist the user's manual edits to the package."""
    if draft.status == "submitted":
        raise DraftError("This application was already submitted")
    if cover_letter is not None:
        draft.cover_letter = cover_letter
    if tailored_cv is not None:
        draft.tailored_cv = tailored_cv
    db.add(draft)
    db.commit()
    db.refresh(draft)
    return draft


def submit_draft(
    db: Session,
    draft: ApplicationDraft,
    method: str,
    profile: Profile,
    *,
    user_id,
) -> Application:
    """
    Submit the reviewed package.

    - email:   sends cover letter + tailored CV PDFs (plus the original CV) via
               Resend when the job published an application email
    - browser / manual: queues as manual_pending with the apply URL; the UI
               opens the posting with the package ready to paste
    """
    if draft.status == "submitted":
        raise DraftError("This application was already submitted")
    if draft.status != "ready" or not draft.cover_letter:
        raise DraftError("Draft is not ready — prepare or fix it first")

    job: JobPosting = draft.job
    if job is None:
        job = db.query(JobPosting).filter(JobPosting.id == draft.job_id).first()
    # TENANCY LAYER 1: profile is a required parameter — the outbound
    # identity (sender name, attached CVs) comes from exactly the profile
    # the route resolved for the caller. This function never looks one up;
    # the optional-lookup version here is where a wrong user's CV got
    # emailed to an employer.

    target_email = job.application_email
    apply_url = job.application_url or job.url
    if method == "email" and not target_email:
        # No published email — degrade to the browser flow
        method = "browser"

    applicant = profile.full_name if profile else None
    application = Application(
        user_id=user_id if user_id is not None else draft.user_id,
        job_id=job.id,
        match_id=draft.match_id,
        draft_id=draft.id,
        method=method,
        status="queued",
        subject=f"Application: {job.title}" + (f" — {applicant}" if applicant else ""),
        body=draft.cover_letter,
        target_email=target_email,
        apply_url=apply_url,
    )
    db.add(application)
    db.flush()

    if method == "email":
        _send_with_pdfs(db, application, draft, job, profile)
    else:
        application.status = "manual_pending"

    # State follows the outcome: a FAILED email send leaves the draft
    # editable, so "Finish applying" still shows it. Applied-ness is DERIVED
    # from the applications table per user — job.status is never written
    # here (it's shared across users).
    if application.status != "failed":
        draft.status = "submitted"
    else:
        draft.status = "ready"
    db.add(draft)
    db.commit()
    db.refresh(application)
    return application


def _send_with_pdfs(
    db: Session,
    application: Application,
    draft: ApplicationDraft,
    job: JobPosting,
    profile: Optional[Profile],
) -> None:
    """Attach tailored cover letter + CV PDFs (and the original CV file) and send."""
    from app.core.config import settings

    if not settings.RESEND_API_KEY or not settings.APPLY_FROM_EMAIL:
        application.status = "failed"
        application.error = (
            "Email apply not configured — set RESEND_API_KEY and APPLY_FROM_EMAIL "
            "in backend/.env, or use 'Apply in browser'"
        )
        return

    import base64
    import os

    try:
        import resend

        applicant = profile.full_name if profile else None
        attachments = []
        os.makedirs(DRAFT_DIR, exist_ok=True)
        for filename, blob in (
            (f"Cover Letter - {applicant or 'Applicant'}.pdf",
             pdf_service.cover_letter_pdf(draft.cover_letter or "", applicant)),
            (f"CV - {applicant or 'Applicant'} (tailored).pdf",
             pdf_service.tailored_cv_pdf(draft.tailored_cv or "", applicant)),
        ):
            attachments.append(
                {"filename": filename, "content": base64.b64encode(blob).decode("utf-8")}
            )

        # Also attach the original CV PDF when available (storage-aware)
        from app.services.storage import read_original_cv

        original_cv = read_original_cv(profile)
        if original_cv:
            attachments.append(
                {
                    "filename": profile.cv_file_name or "CV.pdf",
                    "content": base64.b64encode(original_cv).decode("utf-8"),
                }
            )

        resend.api_key = settings.RESEND_API_KEY
        from_email = (
            f"{applicant} <{settings.APPLY_FROM_EMAIL}>" if applicant else settings.APPLY_FROM_EMAIL
        )
        email = resend.Emails.send(
            {
                "from": from_email,
                "to": [application.target_email],
                "subject": application.subject,
                "text": draft.cover_letter or "",
                "attachments": attachments,
            }
        )
        application.status = "sent"
        application.sent_at = utc_now()
        logger.info("Tailored application sent to %s (id=%s)", application.target_email, email.get("id"))
    except Exception as e:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Email send failed: {e}"
        logger.error("Tailored application send failed: %s", e)
