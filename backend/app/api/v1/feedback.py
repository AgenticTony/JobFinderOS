"""Feedback endpoint — the beta testers' one-box form.

Auth-only (the row is account-linked BY DESIGN, disclosed on the page),
rate-limited so a stuck submit button can't flood the owner's inbox.
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.database import get_db
from app.core.ratelimit import enforce
from app.models import User
from app.schemas.feedback import FeedbackAck, FeedbackCreate
from app.services import feedback_service

router = APIRouter()


@router.post("", response_model=FeedbackAck)
def submit_feedback(
    payload: FeedbackCreate,
    user: User = Depends(get_authenticated_user),
    db: Session = Depends(get_db),
) -> FeedbackAck:
    enforce(user.id, "feedback")
    feedback = feedback_service.create_feedback(db, user=user, payload=payload)
    notified = feedback_service.notify_owner(feedback, user_email=user.email)
    return FeedbackAck(notified=notified)
