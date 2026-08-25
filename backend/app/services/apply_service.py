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
import os
from datetime import datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Application, JobPosting
from app.services.cv_service import get_active_profile

logger = logging.getLogger(__name__)


class ApplyError(Exception):
    """Raised when an application cannot be retried."""


def retry_application(db: Session, application: Application) -> Application:
    """Retry sending a failed email application."""
    if application.method != "email":
        raise ApplyError("Only email applications can be retried")
    job = db.query(JobPosting).filter(JobPosting.id == application.job_id).first()
    profile = get_active_profile(db)
    application.error = None
    _send_email_application(db, application, job, profile)
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

        attachments = []
        if profile and profile.cv_file_path and os.path.exists(profile.cv_file_path):
            with open(profile.cv_file_path, "rb") as fh:
                attachments.append(
                    {
                        "filename": profile.cv_file_name or "CV.pdf",
                        "content": base64.b64encode(fh.read()).decode("utf-8"),
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
        application.sent_at = datetime.utcnow()
        logger.info("Application email sent to %s (id=%s)", application.target_email, email.get("id"))
    except Exception as e:  # noqa: BLE001
        application.status = "failed"
        application.error = f"Email send failed: {e}"
        logger.error("Application email failed: %s", e)
