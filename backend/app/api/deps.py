"""Shared API dependencies: authenticated user + scoped profile access."""

import logging

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Profile, User
from app.models import User as UserModel
from app.services.cv_service import get_active_profile
from app.users import current_active_user

logger = logging.getLogger(__name__)


def get_authenticated_user(user: UserModel = Depends(current_active_user)) -> User:
    """Every business route starts here — the caller's account."""
    return user


def get_user_profile(
    db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
) -> Profile:
    """The caller's profile, or a 404 that tells them to upload a CV."""
    profile = get_active_profile(db, user_id=user.id)
    if profile is None:
        raise HTTPException(status_code=404, detail="No CV on file — upload one first")
    return profile


def owns_or_404(resource_user_id, user: User, what: str) -> None:
    """IDOR guard: a row's user_id must match the caller's."""
    if resource_user_id is not None and str(resource_user_id) != str(user.id):
        raise HTTPException(status_code=404, detail=f"{what} not found")
