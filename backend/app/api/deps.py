"""Shared API dependencies: authenticated user + scoped profile access."""

import logging

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.ratelimit import enforce
from app.models import Profile, User
from app.models import User as UserModel
from app.services.cv_service import get_active_profile
from app.users import current_active_user

logger = logging.getLogger(__name__)


async def register_rate_limit(request: Request) -> None:
    """Per-address signup throttle. Keyed by the SUBMITTED email, not IP:
    per-address is the meaningful unit for registration hammering, and
    every client behind one proxy would otherwise share a bucket.
    Pre-reading the body is safe — Starlette caches json()/form()."""
    email = ""
    try:
        body = await request.json()
        email = str(body.get("email", ""))
    except Exception:  # noqa: BLE001 — malformed body falls through to the route's own 422
        pass
    enforce(f"reg:{email.lower()}", "auth_register")


async def login_rate_limit(request: Request) -> None:
    """Per-account login throttle — the core brute-force guard. The login
    form's `username` field carries the email."""
    username = ""
    try:
        form = await request.form()
        username = str(form.get("username", ""))
    except Exception:  # noqa: BLE001 — malformed form falls through to the route's own 422
        pass
    enforce(f"login:{username.lower()}", "auth_login")


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
    """IDOR guard: a row's user_id must match the caller's.

    FAILS CLOSED: a NULL user_id is treated as 'nobody's row' and rejected.
    The old `is not None` check passed NULL rows to every authenticated
    user — the database columns are nullable (pre-backfill rows), so the
    guard must be stricter than the schema, not looser.
    """
    if resource_user_id is None or str(resource_user_id) != str(user.id):
        raise HTTPException(status_code=404, detail=f"{what} not found")


def set_user_context_middleware(request, call_next):
    """WO-04/WO-05: stamp the authenticated caller into request context
    so ai_usage rows attribute cost per user. FastAPI dependency
    resolution happens later, so decode the JWT best-effort here — no
    verification cost, just the claim; auth itself stays at the routes.
    """
    from app.services.ai_service import current_user_id

    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        try:
            import base64
            import json as _json

            payload = auth.split(" ")[1].split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = _json.loads(base64.urlsafe_b64decode(payload))
            sub = claims.get("sub")
            if sub:
                import uuid as _uuid

                current_user_id.set(_uuid.UUID(sub))
        except Exception:  # noqa: BLE001 — context stamping is best-effort
            pass
    return call_next(request)
