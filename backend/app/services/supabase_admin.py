"""Supabase Auth admin API — the service-key side channel.

The browser talks to Supabase directly; this module is the backend's
admin path for the one operation that needs the service role key:

  - delete_user: GDPR erasure of the identity (email + password hash
    live in Supabase, so local cascade alone is incomplete erasure).
    Session revocation comes WITH the hard delete (deleting a user
    removes their auth.sessions rows) — there is no separate admin
    sign-out-by-user-id route; the earlier call to one was a silent
    404 no-op (review finding 2026-09-08).

Direct REST via httpx (same contract as onboarding_service): the admin
surface is stable, and it keeps one HTTP style for these side channels.
NEVER raise into the caller — return bool and let the caller decide
(see account.delete_account for the erasure ordering contract).
"""

import logging

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

AUTH_API = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"


def _headers() -> dict:
    return {
        "apikey": settings.SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {settings.SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def delete_user(user_id: str) -> bool:
    """Hard-delete the Supabase auth user (the admin API default —
    shouldSoftDelete=false — is exactly what GDPR erasure wants; verified
    in MIGRATION.md's doc check). Deleting the user removes their
    sessions with it.
    """
    if not settings.SUPABASE_URL or not settings.SUPABASE_SERVICE_KEY:
        logger.warning(
            "supabase_admin: SUPABASE_URL/SERVICE_KEY unset — identity "
            "delete skipped for %s", user_id,
        )
        return False
    try:
        resp = httpx.delete(
            f"{AUTH_API}/admin/users/{user_id}",
            headers=_headers(),
            timeout=10,
        )
        if resp.status_code in (200, 204):
            return True
        # 404: already gone (idempotent erasure — e.g. a retry after a
        # partial failure). Anything else is a real failure the caller
        # must not silently swallow.
        if resp.status_code == 404:
            return True
        logger.error(
            "supabase_admin: identity delete for %s -> %s %s",
            user_id, resp.status_code, resp.text[:120],
        )
        return False
    except Exception:  # noqa: BLE001
        logger.exception("supabase_admin: identity delete failed for %s", user_id)
        return False
