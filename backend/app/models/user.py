"""User account model — fastapi-users base (UUID id, email, hashed password,
is_active/is_superuser/is_verified) on the app's shared Base.

Phase 0: the account exists and authenticates. Phase 1 links Profile and all
per-user rows to this table (user_id FKs).
"""


from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Column, DateTime, Integer, String

from app.core.orm import Base
from app.core.timeutil import utc_now


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    # Space for Phase 1+ account fields (stripe_customer_id, plan, etc.)
    display_name = Column(String(120), nullable=True)
    # P1-7: JWT revocation generation. Every token embeds the value at
    # issue time ('ver' claim); auth rejects a token whose claim no longer
    # matches the row. Password changes bump the version, killing every
    # outstanding token (fastapi-users' stock JWT is otherwise unrevokable
    # until expiry). 0 = the pre-column default, also how claim-less
    # legacy tokens read.
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime, default=utc_now, nullable=False)  # python-side: sqlite has no now()

    def __repr__(self):  # pragma: no cover
        return f"<User {self.email} {self.id}>"
