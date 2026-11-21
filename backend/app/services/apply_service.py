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

# J-2: the send-failure exception and the two user-facing messages have
# ONE home (draft_service, the primary send path) — this module's retry
# paths raise/honour the same literals so the two flows cannot drift.
# J-6: the idempotency-keyed dispatch and the known/unknown outcome
# split live there too, for the same reason.
from app.services.draft_service import (
    CV_UNREADABLE_MESSAGE,
    SEND_FAILED_MESSAGE,
    OriginalCvUnreadableError,
    _dispatch_error_message,
    _idempotency_key,
    _resend_send,
)

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
    # Beta gate (owner decision 2026-09-01) — same rule as submit_draft:
    # no platform-sender dispatches while email apply is retooled to the
    # user's own Gmail.
    if not settings.EMAIL_APPLY_ENABLED:
        raise ApplyError(
            "Email apply is moving to your own Gmail account and returns "
            "at the end of beta"
        )
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
    # SUBMIT: a draft mid-dispatch ('sending') is owned by another send —
    # retrying alongside it is the same double-send the claim prevents.
    if draft is not None and draft.status == "sending":
        raise ApplyError("This application is already being submitted")
    if draft and draft.cover_letter is not None:
        from app.services.draft_service import _send_with_pdfs

        _send_with_pdfs(db, application, draft, job, profile)
    else:
        _send_email_application(db, application, job, profile)

    # Mirror the submit state machine: a failed retry leaves the draft
    # ready (editable, still actionable); a SUCCESSFUL retry completes the
    # submission (P1-2) — the draft must leave the sendable pool exactly
    # like submit_draft() does, or the UI keeps "Finish applying" alive
    # and re-sends the employer a duplicate.
    if draft is not None:
        draft.status = "ready" if application.status == "failed" else "submitted"
        db.add(draft)
    db.commit()
    db.refresh(application)
    return application


def _send_email_application(db: Session, application: Application, job: JobPosting, profile) -> None:
    """Send the application email via Resend and update status."""
    if not settings.RESEND_API_KEY:
        application.status = "failed"
        # P0-6: environment-neutral — "edit backend/.env" was a dead end
        # in the Render container, exactly where this error fires.
        application.error = (
            "RESEND_API_KEY not configured — email apply is not "
            "configured on this deployment; contact the operator"
        )
        return

    if not settings.APPLY_FROM_EMAIL:
        application.status = "failed"
        application.error = "APPLY_FROM_EMAIL not configured (verified sender, e.g. you@yourdomain.com)"
        return

    # P1-3(c): the per-account daily ceiling on employer emails — before
    # any attachment work or dispatch (same cap as the draft send path).
    from app.core.ratelimit import enforce

    enforce(application.user_id, "send_daily")

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
        if original_cv is None:
            # 2026-10-30 audit (D1): this email's whole payload IS the
            # original CV. The old `if original_cv:` dispatched an
            # attachment-less application email, marked it 'sent', and
            # left no trace. Raising lands in the except below: failed,
            # retryable, nothing dispatched. J-2: the typed exception
            # carries its own advice branch (re-upload) — the raised
            # text itself is server-side only.
            raise OriginalCvUnreadableError(
                "Original CV file could not be read — nothing was sent"
            )
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
        # P1-1: replies must reach the APPLICANT, not the shared
        # APPLY_FROM_EMAIL inbox (same fix as the draft send path).
        if profile is not None and getattr(profile, "email", None):
            params["reply_to"] = profile.email
    except OriginalCvUnreadableError as e:
        # J-2: the send never left — the message says what to do first
        # (re-upload), because a plain retry would fail the same way.
        application.status = "failed"
        application.error = CV_UNREADABLE_MESSAGE
        logger.error(
            "Application email aborted — original CV unreadable (%s): %s",
            type(e).__name__, e, exc_info=True,
        )
        return
    except Exception as e:
        # J-2: the failure fired BEFORE anything was dispatched, so
        # "the email was not sent" is the truth. The exception's text
        # and type are server-side knowledge — logged with the
        # traceback, never copied into the error column.
        application.status = "failed"
        application.error = SEND_FAILED_MESSAGE
        logger.error(
            "Application email failed before dispatch (%s): %s",
            type(e).__name__, e, exc_info=True,
        )
        return

    # The dispatch itself. J-6: only a rejection Resend ANSWERED proves
    # nothing was accepted — a timeout or a dropped connection may have
    # delivered, so the message claims no more than is known.
    # J-8: the key covers every value in `params` (see
    # draft_service._idempotency_key), and the dispatch refuses to go
    # out without one.
    try:
        email = _resend_send(
            resend, params, _idempotency_key(application.id, params)
        )
    except Exception as e:
        application.status = "failed"
        application.error = _dispatch_error_message(e)
        logger.error(
            "Application email failed (%s): %s",
            type(e).__name__, e, exc_info=True,
        )
        return
    application.status = "sent"
    application.sent_at = utc_now()
    logger.info("Application email sent to %s (id=%s)", application.target_email, email.get("id"))
