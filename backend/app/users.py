"""
Auth layer — MIG-WO2: Supabase Auth JWT verification (no fastapi-users).

The browser talks to Supabase Auth directly (sign-up, sign-in, password
reset, email verification — all hosted, all native). This module is the
backend's half: verify the Bearer token against the project's JWKS and
resolve it to a User row.

JWKS, not a shared secret: this project (created 2026-08) signs with an
asymmetric ES256 key published at
  {SUPABASE_URL}/auth/v1/.well-known/jwks.json
(live-verified 2026-09-08: one EC key, alg ES256 — NOT the RS256 that
MIGRATION.md's 2026-08-27 doc check assumed; the algorithm is read from
the key, never hardcoded). Verification is pure CPU — no call to the
Auth server, no secret on our side.

Mirror rows: Supabase owns identities; the `users` table remains the FK
anchor for profiles/matches/drafts/applications. get_authenticated_user
upserts the mirror on first sight (id + email from the token claims) and
runs the registration side-effects that fastapi-users' on_after_register
hook used to own (Profile row + onboarding drip).

Deleted with this module's fastapi-users past: the three auth routers
(register/login/users — Supabase's hosted pages+API own those flows),
the async engine + ASYNC_DATABASE_URL dual-database machinery (it existed
ONLY for fastapi-users' async adapter), the P1-7 token_version
revocation scheme (Supabase terminates sessions on password change —
doc-verified 2026-09-08), and the auth signup hardening (password policy
and auth rate limits live in Supabase's service now).
"""

import logging
import threading
import time
import uuid
from typing import Optional

import httpx
import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models import User

logger = logging.getLogger(__name__)

# Token checks (Supabase access tokens: iss is the auth base URL, aud is
# the role claim audience; exp is enforced by PyJWT with a small skew
# allowance for the API server's clock).
AUDIENCE = "authenticated"
ISSUER = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1"
CLOCK_SKEW_SECONDS = 30

# JWKS cache. Keys rotate rarely; refresh when a token names a kid we
# don't have (rotation moment) or the cache goes stale.
_JWKS_TTL_SECONDS = 3600
_jwks_lock = threading.Lock()
_jwks_cache: dict = {}
_jwks_fetched_at: float = 0.0


def fetch_jwks() -> dict:
    """Fetch (and cache) the project's public signing keys.

    Module-level and single-point so tests can monkeypatch it with a
    fixture keypair (see conftest.py) — nothing else in the app performs
    this fetch.
    """
    global _jwks_cache, _jwks_fetched_at
    with _jwks_lock:
        now = time.monotonic()
        if _jwks_cache and now - _jwks_fetched_at < _JWKS_TTL_SECONDS:
            return _jwks_cache
        url = f"{settings.SUPABASE_URL.rstrip('/')}/auth/v1/.well-known/jwks.json"
        resp = httpx.get(url, timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
        _jwks_fetched_at = now
        return _jwks_cache


def _invalidate_jwks_cache() -> None:
    """Test hook: drop the cache so the next fetch_jwks() hits the wire
    (or the monkeypatch)."""
    global _jwks_cache, _jwks_fetched_at
    with _jwks_lock:
        _jwks_cache = {}
        _jwks_fetched_at = 0.0


def _key_for_kid(token: str) -> tuple:
    """Return (verification_key, algorithm) for the token's kid.

    Refreshes the JWKS once when a token carries an unknown kid — the
    key-rotation moment — before giving up.
    """
    header = pyjwt.get_unverified_header(token)
    kid = header.get("kid")
    alg = header.get("alg")
    keys = fetch_jwks().get("keys", [])
    for k in keys:
        if kid is None or k.get("kid") == kid:
            return k, alg or k.get("alg", "ES256")
    _invalidate_jwks_cache()
    keys = fetch_jwks().get("keys", [])
    for k in keys:
        if kid is None or k.get("kid") == kid:
            return k, alg or k.get("alg", "ES256")
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid authentication token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def verify_supabase_token(token: str) -> dict:
    """Verify a Supabase access token; return its claims.

    Fail-closed: signature (JWKS), issuer, audience and expiry must all
    check out. Any failure is a 401 — the frontend treats 401 as "clear
    the session, go to login" (unchanged contract from the JWT era).
    """
    try:
        key, alg = _key_for_kid(token)
        public_key = pyjwt.algorithms.RSAAlgorithm if alg.startswith("RS") else pyjwt.algorithms.ECAlgorithm
        pem = public_key.from_jwk(key)
        claims = pyjwt.decode(
            token,
            key=pem,
            algorithms=[alg],
            audience=AUDIENCE,
            issuer=ISSUER,
            leeway=CLOCK_SKEW_SECONDS,
        )
    except HTTPException:
        raise
    except Exception as exc:  # PyJWTError, KeyError, ValueError — all 401
        logger.info("Rejected auth token: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    return claims


def get_authenticated_user(
    request: Request, db: Session = Depends(get_db)
) -> User:
    """Every business route starts here — the caller's account.

    Bearer token → Supabase claims → mirror row. The mirror row is
    upserted on first sight: SELECT by id, INSERT when missing (email
    from the token; hashed_password is a sentinel — Supabase owns the
    real secret). First creation also runs the side-effects registration
    used to own: the Profile row and the onboarding drip.
    """
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = verify_supabase_token(auth.removeprefix("Bearer ").strip())

    try:
        user_id = uuid.UUID(str(claims.get("sub", "")))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    user = db.get(User, user_id)
    if user is None:
        user = User(
            id=user_id,
            email=str(claims.get("email") or f"{user_id}@unknown.invalid"),
            hashed_password="supabase-auth",  # sentinel: column is NOT NULL
        )
        db.add(user)
        _on_first_sight(user, db, request)
        # get_db never commits (routes own their commits); a dependency
        # that writes must commit its own work or the mirror row dies
        # with the session — and the NEXT request re-inserts into a
        # unique-constraint collision.
        db.commit()
        logger.info("Mirror row created for Supabase user %s", user_id)
    if not user.is_active:
        # Tombstone (GDPR erasure): the account is dead. Supabase's
        # access tokens stay signature-valid up to ~1h after the
        # identity is deleted — the tombstone is what turns that window
        # into 401s instead of resurrected mirror rows.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def _on_first_sight(user: User, db: Session, request: Request) -> None:
    """Registration side-effects, moved from on_after_register.

    Every account gets its Profile row (the per-user world hangs off
    this link), and the beta onboarding drip fires best-effort
    (Resend contact + user.created event; language picked by the
    browser Accept-Language — same contract as the fastapi-users hook).
    Runs uncommitted inside the caller's transaction; get_authenticated_
    user commits the mirror + profile together.
    """
    from app.models import Profile as ProfileModel

    if not db.query(ProfileModel).filter(ProfileModel.user_id == user.id).first():
        db.add(ProfileModel(user_id=user.id, is_active=1))
    try:
        from app.services import onboarding_service

        onboarding_service.notify_signup(
            str(user.email),
            first_name=None,
            accept_language=request.headers.get("accept-language"),
        )
    except Exception:  # noqa: BLE001 — never fail a first request over email
        logger.exception("onboarding: signup notify failed for %s", user.email)


# current_active_user is imported across the API layer (deps.py); keep
# the name as an alias so the swap is one import, not twelve.
current_active_user = get_authenticated_user


# Optional dependency retained for any future "maybe-authenticated"
# route; returns None instead of 401.
def get_optional_user(
    request: Request, db: Session = Depends(get_db)
) -> Optional[User]:
    if not request.headers.get("authorization", "").startswith("Bearer "):
        return None
    try:
        return get_authenticated_user(request, db)
    except HTTPException:
        return None
