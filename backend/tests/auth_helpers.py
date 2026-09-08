"""MIG-WO2 test auth infrastructure — minted Supabase-shaped tokens.

The suite never talks to Supabase. Instead:
  - a fixture EC P-256 keypair (generated once per test process) plays
    the project's signing key;
  - the first use patches app.users.fetch_jwks to serve its public JWK
    (same {keys:[...]} shape as the live
    /auth/v1/.well-known/jwks.json — one EC key, alg ES256, live-
    verified 2026-09-08);
  - mint_token signs ES256 access tokens carrying the claims the real
    ones carry: sub, email, aud="authenticated", iss={SUPABASE_URL}/auth/v1,
    role, exp.

`register`/`auth_client` mirror the old fastapi-users test helpers'
contracts (create the mirror row; return the user id / set the Bearer
header), so the four test modules that had local copies swap their
bodies, not their call sites.

Nothing here imports app.* at module level — conftest.py owns the env
BEFORE any app import (the DATABASE_URL discipline), and that ordering
must survive this file being imported by conftest-adjacent modules.
"""

import json
import time
import uuid

_FIXTURE: dict = {}  # {"private": key_obj, "jwks": {"keys": [...]}, "kid": ...}


def _ensure_fixture() -> dict:
    """Generate the keypair (once) and patch app.users.fetch_jwks."""
    if _FIXTURE:
        return _FIXTURE
    import jwt as pyjwt
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(pyjwt.algorithms.ECAlgorithm.to_jwk(private))
    jwk.update({"kid": "test-signing-key-1", "alg": "ES256", "use": "sig"})

    from app import users as app_users

    # Replace the cached network fetch wholesale — the cache, the TTL and
    # the wire call all belong to the function being replaced.
    app_users.fetch_jwks = lambda: {"keys": [jwk]}

    _FIXTURE.update(
        {"private": private, "jwk": jwk, "kid": jwk["kid"]}
    )
    return _FIXTURE


def mint_token(
    user_id=None,
    email: str = "user@example.com",
    lifetime_seconds: int = 3600,
    **claim_overrides,
) -> str:
    """An ES256 Supabase access token for (user_id, email).

    claim_overrides replace defaults (exp lifetime, wrong aud/iss for
    negative tests) — anything EXCEPT sub, which callers set explicitly.
    """
    fx = _ensure_fixture()
    import jwt as pyjwt

    from app.users import ISSUER

    now = int(time.time())
    payload = {
        "sub": str(user_id or uuid.uuid4()),
        "email": email,
        "aud": "authenticated",
        "iss": ISSUER,
        "role": "authenticated",
        "iat": now,
        "exp": now + lifetime_seconds,
    }
    payload.update(claim_overrides)
    return pyjwt.encode(
        payload,
        fx["private"],
        algorithm="ES256",
        headers={"kid": fx["kid"]},
    )


def get_or_create_user(email: str) -> uuid.UUID:
    """The mirror row's id, creating the row (plus its Profile — the
    same rows first-sight creates in production) if missing. FK-holding
    test rows need the users row to exist; dozens of tests seed the
    Profile by query the way on_after_register used to leave it.
    Returns the ID (not the instance): commit expires instances and
    close() detaches them, so a returned User would blow up on the
    caller's first attribute read."""
    from app.core.database import SessionLocal
    from app.models import Profile, User

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            user = User(id=uuid.uuid4(), email=email, hashed_password="supabase-auth")
            db.add(user)
            db.flush()
            if (
                db.query(Profile).filter(Profile.user_id == user.id).first()
                is None
            ):
                db.add(Profile(user_id=user.id, is_active=1))
        db.flush()
        return user.id  # read while the session is open
    finally:
        db.commit()  # commit doesn't detach; close below does
        db.close()


def register(client, email: str) -> str:
    """Old _register contract: create the account, return its id (str)."""
    return str(get_or_create_user(email))


def auth_client(client, email: str) -> str:
    """Old _auth_client contract: mint + set the Bearer header, return
    the token."""
    uid = get_or_create_user(email)
    token = mint_token(uid, email)
    client.headers.update({"Authorization": f"Bearer {token}"})
    return token


def stub_supabase_delete(monkeypatch, result: bool = True) -> None:
    """Make GDPR erasure tests hermetic: the identity delete would
    otherwise call Supabase with the (unset) service key and 503.

    result=False simulates a Supabase outage to pin the 503-before-
    local-deletion ordering."""
    from app.services import supabase_admin

    monkeypatch.setattr(supabase_admin, "delete_user", lambda uid: result)


def first_sight_request(
    client,
    email: str,
    path: str = "/api/v1/profile/me",
    extra_headers: dict | None = None,
):
    """Simulate a brand-new account's FIRST authenticated request:
    no mirror row exists, so get_authenticated_user creates it and runs
    the first-sight side effects (Profile row, onboarding drip).

    Returns (response, user_id) — typically (200, uid) with an EMPTY
    profile: first-sight creates the row, and /profile/me answers 200
    for an existing-but-onboarding profile (404 is only for no row).
    This is the path Supabase signup lands on: the browser signs in, the
    console fires its first API call, and the backend meets the user.
    extra_headers ride along (Accept-Language picks the drip language)."""
    uid = uuid.uuid4()
    token = mint_token(user_id=uid, email=email)  # no mirror row anywhere
    headers = {"Authorization": f"Bearer {token}", **(extra_headers or {})}
    resp = client.get(path, headers=headers)
    return resp, uid
