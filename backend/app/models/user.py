"""User account model — fastapi-users base (UUID id, email, hashed password,
is_active/is_superuser/is_verified) on the app's shared Base.

Phase 0: the account exists and authenticates. Phase 1 links Profile and all
per-user rows to this table (user_id FKs).
"""


from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import Column, DateTime, String

from app.core.database import Base
from app.core.timeutil import utc_now


class User(SQLAlchemyBaseUserTableUUID, Base):
    __tablename__ = "users"

    # Space for Phase 1+ account fields (stripe_customer_id, plan, etc.)
    display_name = Column(String(120), nullable=True)
    created_at = Column(DateTime, default=utc_now, nullable=False)  # python-side: sqlite has no now()

    def __repr__(self):  # pragma: no cover
        return f"<User {self.email} {self.id}>"
