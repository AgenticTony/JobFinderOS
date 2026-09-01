"""Feedback service — persist the row, notify the owner (best-effort).

The DB row is the source of truth. The email to the owner is a
convenience so feedback is seen the same day instead of discovered in
a table three weeks later; any failure to send is logged and swallowed
— the submitter must never lose their feedback to a mail hiccup.
"""

import logging

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Feedback, User
from app.schemas.feedback import FeedbackCreate

logger = logging.getLogger(__name__)


def create_feedback(db: Session, *, user: User, payload: FeedbackCreate) -> Feedback:
    feedback = Feedback(
        user_id=user.id, category=payload.category, message=payload.message.strip()
    )
    db.add(feedback)
    db.commit()
    db.refresh(feedback)
    return feedback


def notify_owner(feedback: Feedback, *, user_email: str | None) -> bool:
    """Email the owner about new feedback. Best-effort: returns False
    (never raises) when Resend isn't wired or the send fails — the row
    is already stored."""
    if not settings.RESEND_API_KEY or not settings.APPLY_FROM_EMAIL:
        return False
    try:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        resend.Emails.send(
            {
                "from": f"JobFinderOS feedback <{settings.APPLY_FROM_EMAIL}>",
                "to": [settings.FEEDBACK_NOTIFY_EMAIL],
                "reply_to": user_email,
                "subject": f"[beta feedback:{feedback.category}] {feedback.message[:60]}",
                "text": (
                    f"category: {feedback.category}\n"
                    f"account: {user_email or feedback.user_id}\n"
                    f"at: {feedback.created_at.isoformat()}\n\n"
                    f"{feedback.message}\n"
                ),
            }
        )
        return True
    except Exception:  # noqa: BLE001 — notification must never break submit
        logger.exception("Feedback notification failed (row %s stored)", feedback.id)
        return False
