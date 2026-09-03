"""
Auth layer — fastapi-users v15 on SQLAlchemy (async adapter, per official
docs: fastapi-users.github.io/fastapi-users/latest/configuration/databases/sqlalchemy/).

The app's main engine is sync; auth runs on a second, async engine over the
same database (psycopg / aiosqlite — see core.database.async_database_url).

Phase 0 scope: register + login (JWT bearer) + /users/me. Email verification
and password-reset routers are intentionally not mounted yet — they require a
mailer; they'll be enabled with the Composio/Resend email work in Phase 2.

P1-7: tokens are revocable via a token_version claim — see
VersionedJWTStrategy and UserManager._bump_token_version below. The stock
fastapi-users JWT is valid until expiry no matter what happens to the
account; with the JWT in localStorage and no refresh flow, a password
change is the user's only "log everything else out" lever, and it used to
do nothing.
"""

import logging
import uuid
from typing import AsyncGenerator, Optional

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import ASYNC_DATABASE_URL
from app.models import User

logger = logging.getLogger(__name__)

# --- Async engine/session for auth (see module docstring) ---
auth_engine = create_async_engine(ASYNC_DATABASE_URL)
AsyncSessionLocal = async_sessionmaker(
    auth_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_user_db(session: AsyncSession = Depends(get_async_session)):
    yield SQLAlchemyUserDatabase(session, User)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.AUTH_SECRET
    verification_token_secret = settings.AUTH_SECRET

    async def validate_password(self, password: str, user) -> None:
        """Password policy (signup hardening). fastapi-users' default
        accepts ANY string — 'a' registered fine. Runs on register AND on
        every future password set/reset. Raises InvalidPasswordException,
        which the routers map to a 400 carrying the reason."""
        from fastapi_users.exceptions import InvalidPasswordException

        n = len(password.encode("utf-8"))
        if n < 8:
            raise InvalidPasswordException(
                reason="Password must be at least 8 characters"
            )
        if n > 72:
            # bcrypt silently truncates beyond 72 bytes — a longer password
            # lends false strength to its prefix
            raise InvalidPasswordException(
                reason="Password must be at most 72 characters"
            )
        local = (user.email or "").split("@")[0].lower()
        if local and len(local) >= 4 and local in password.lower():
            raise InvalidPasswordException(
                reason="Password must not contain your email address"
            )

    async def on_after_register(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        # Every account gets its Profile row at registration — the per-user
        # world hangs off this link
        from app.core.database import SessionLocal
        from app.models import Profile as ProfileModel

        db = SessionLocal()
        try:
            if not db.query(ProfileModel).filter(ProfileModel.user_id == user.id).first():
                db.add(ProfileModel(user_id=user.id, is_active=1))
                db.commit()
        finally:
            db.close()

        # Beta onboarding drip (owner decision 2026-09-03): best-effort
        # create the Resend contact + fire user.created — the automation
        # listening for it runs the daily "how to use this section"
        # series. Best-effort by contract: registration must succeed
        # regardless (see onboarding_service).
        try:
            from app.services import onboarding_service

            onboarding_service.notify_signup(
                str(user.email), first_name=None
            )
        except Exception:  # noqa: BLE001 — never fail a signup over email
            logger.exception(
                "onboarding: signup notify failed for %s", user.email
            )

    async def on_after_update(
        self, user: User, update_dict: dict, request: Optional[Request] = None
    ) -> None:
        # P1-7: fastapi-users v15 changes passwords via PATCH /users/me —
        # update() calls _update (which commits the new hash) and then this
        # hook with the ORIGINAL update dict. A password change must revoke
        # every outstanding token; other fields (display_name, ...) must
        # NOT log the user out.
        if "password" in update_dict:
            await self._bump_token_version(user, why="password change")

    async def on_after_reset_password(
        self, user: User, request: Optional[Request] = None
    ) -> None:
        # The forgot-password router is not mounted yet, but the manager
        # method is reachable — a reset is an even stronger revocation
        # trigger than a change. Wire it now so mounting the router later
        # does not silently reopen the hole.
        await self._bump_token_version(user, why="password reset")

    async def _bump_token_version(self, user: User, *, why: str) -> None:
        """Invalidate every token issued before now: version-pinned tokens
        stop matching the row at the next request (see
        VersionedJWTStrategy.read_token)."""
        user.token_version = int(getattr(user, "token_version", 0) or 0) + 1
        session = getattr(self.user_db, "session", None)
        if session is None:  # pragma: no cover — adapter always carries one
            logger.warning(
                "token_version bump for user %s skipped: auth session "
                "unavailable", user.id,
            )
            return
        session.add(user)
        await session.commit()
        logger.info("token_version bumped for user %s (%s) — outstanding "
                    "tokens revoked", user.id, why)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="api/v1/auth/jwt/login")


class VersionedJWTStrategy(JWTStrategy):
    """JWT strategy carrying the user's token_version as a 'ver' claim.

    The stock fastapi-users JWT is unrevokable until expiry — with a
    multi-day lifetime and the token sitting in localStorage, a password
    change did nothing to a leaked token (P1-7). write_token pins the
    claim to the user's CURRENT version; read_token re-reads the live
    row (it already fetches the user) and rejects the token on mismatch,
    which the bearer transport surfaces as 401 — the frontend already
    treats any 401 as "clear the token, go to login".

    Tokens minted before the column existed carry no 'ver'; they read as
    version 0 so the rollout does not force a mass logout. The first
    password change bumps the row to 1 and still revokes them (0 != 1).
    """

    async def write_token(self, user: User) -> str:
        from fastapi_users.jwt import generate_jwt

        data = {
            "sub": str(user.id),
            "aud": self.token_audience,
            "ver": int(getattr(user, "token_version", 0) or 0),
        }
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_token(
        self, token: Optional[str], user_manager: BaseUserManager[User, uuid.UUID]
    ) -> Optional[User]:
        import jwt as pyjwt
        from fastapi_users import exceptions
        from fastapi_users.jwt import decode_jwt

        if token is None:
            return None
        try:
            data = decode_jwt(
                token, self.decode_key, self.token_audience,
                algorithms=[self.algorithm],
            )
            user_id = data.get("sub")
            if user_id is None:
                return None
        except pyjwt.PyJWTError:
            return None

        try:
            parsed_id = user_manager.parse_id(user_id)
            user = await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None
        if user is None:
            return None

        try:
            token_ver = int(data.get("ver", 0))
        except (TypeError, ValueError):
            return None  # malformed claim — fail closed
        current_ver = int(getattr(user, "token_version", 0) or 0)
        if token_ver != current_ver:
            logger.info(
                "Rejected version-mismatched token for user %s "
                "(token ver=%s, row ver=%d)", user_id, data.get("ver"), current_ver,
            )
            return None
        return user


def get_jwt_strategy() -> JWTStrategy:
    return VersionedJWTStrategy(
        secret=settings.AUTH_SECRET,
        lifetime_seconds=settings.AUTH_TOKEN_LIFETIME_SECONDS,
    )


auth_backend = AuthenticationBackend(
    name="jwt", transport=bearer_transport, get_strategy=get_jwt_strategy
)

fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

current_active_user = fastapi_users.current_user(active=True)


class UserRead(schemas.BaseUser[uuid.UUID]):
    display_name: Optional[str] = None


class UserCreate(schemas.BaseUserCreate):
    display_name: Optional[str] = None


class UserUpdate(schemas.BaseUserUpdate):
    display_name: Optional[str] = None
