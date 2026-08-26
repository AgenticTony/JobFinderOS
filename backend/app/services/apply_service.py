"""
Apply service — retry support for submitted applications.

The primary application path is the DRAFT flow (see draft_service.py):
approve match -> AI tailors CV + cover letter -> user reviews -> submit_draft().
This module now only handles retrying failed email submissions.

Note on automated form-filling: large portals (LinkedIn Easy Apply, Indeed)
prohibit bot-driven submissions in their terms of service. JobFinderOS keeps
a human in the loop for portal applies and only auto-sends where an explicit
application email is published with the posting.
"""

import base64
import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.timeutil import utc_now
from app.models import Application, JobPosting

logger = logging.getLogger(__name__)


class ApplyError(Exception):
    """Raised when an application cannot be retried."""


def retry_application(db: Session, application: Application, profile) -> Application:
    """Retry a failed email application — with the SAME reviewed package.

    TENANCY LAYER 1: the profile arrives as a required parameter — this
    function never resolves identity itself. It previously looked the
    profile up from application.user_id; the outbound CV identity now
    comes from exactly what the route resolved for the caller.

    If the application came from a draft, the retry rebuilds and reattaches
    the tailored cover letter + CV PDFs the user approved (never a weaker
    original-CV-only email). Falls back to the plain email only for
    legacy applications without a draft.
    """
    if application.method != "email":
        raise ApplyError("Only email applications can be retried")
    if profile is None:
        raise ApplyError("No CV on file for this account")
    from app.models import ApplicationDraft

    job = db.query(JobPosting).filter(JobPosting.id == application.job_id).first()
    application.error = None

    draft = (
        db.query(ApplicationDraft).filter(ApplicationDraft.id == application.draft_id).first()
        if application.draft_id
        else None
    )
    if draft and draft.cover_letter is not None:
        from app.services.draft_service import _send_with_pdfs

        _send_with_pdfs(db, application, draft, job, profile)
    else:
        _send_email_application(db, application, job, profile)

    # Mirror the submit state machine: a failed retry leaves the draft ready
    if application.status == "failed" and draft is not None:
        draft.status = "ready"
        db.add(draft)
    db.commit()
    db.refresh(application)
    return application


def _send_email_application(db: Session, application: Application, job: JobPosting, profile) -> None:
    """Send the application email via Resend and update status."""
    if not settings.RESEND_API_KEY:
        application.status = "failed"
        application.error = (
            "RESEND_API_KEY not configured — set it in backend/.env to enable email applications"
        )
        return

    if not settings.APPLY_FROM_EMAIL:
        application.status = "failed"
        application.error = "APPLY_FROM_EMAIL not configured (verified sender, e.g. you@yourdomain.com)"
        return

    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        from_email = (
            f"{profile.full_name} <{settings.APPLY_FROM_EMAIL}>"
            if profile and profile.full_name
            else settings.APPLY_FROM_EMAIL
        )

        from app.services.storage import read_original_cv

        attachments = []
        original_cv = read_original_cv(profile)
        if original_cv:
            attachments.append(
                {
                    "filename": profile.cv_file_name or "CV.pdf",
                    "content": base64.b64encode(original_cv).decode("utf-8"),
                }
            )

        params: dict = {
            "from": from_email,
            "to": [application.target_email],
            "subject": application.subject,
            "text": application.body or "",
        }
        if attachments:
            params["attachments"] = attachments

        email = resend.Emails.send(params)
        application.status = "sent"
        application.sent_at = utc_now()
        logger.info("Application email sent to %s (id=%s)", application.target_email, email.get("id"))
    except Exception as e:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Email send failed: {e}"
        logger.error("Application email failed: %s", e)
