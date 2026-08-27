"""
Auth layer — fastapi-users v15 on SQLAlchemy (async adapter, per official
docs: fastapi-users.github.io/fastapi-users/latest/configuration/databases/sqlalchemy/).

The app's main engine is sync; auth runs on a second, async engine over the
same database (psycopg / aiosqlite — see core.database.async_database_url).

Phase 0 scope: register + login (JWT bearer) + /users/me. Email verification
and password-reset routers are intentionally not mounted yet — they require a
mailer; they'll be enabled with the Composio/Resend email work in Phase 2.
"""

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


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
):
    yield UserManager(user_db)


bearer_transport = BearerTransport(tokenUrl="api/v1/auth/jwt/login")


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(
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
