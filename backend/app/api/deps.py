"""Shared API dependencies: authenticated user + scoped profile access.

MIG-WO2: the register/login rate-limit dependencies are gone — signup
and login moved to Supabase Auth (browser → Supabase directly), which
enforces its own password policy and auth rate limits. The per-user
spend buckets in core/ratelimit.py are untouched; see app/users.py.
"""

import logging

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Profile, User
from app.services.cv_service import get_active_profile
from app.users import get_authenticated_user  # noqa: F401 — re-exported

logger = logging.getLogger(__name__)


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
