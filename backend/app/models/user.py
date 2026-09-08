"""User account model — MIG-WO2: a local mirror of the Supabase Auth user.

Supabase owns identities (passwords, sessions, verification). This table
remains the FK anchor for every per-user row (profiles, match_results,
application_drafts, applications): app/users.py upserts the mirror on a
user's first authenticated request.

Columns match the physical table exactly (created by the initial schema
migration under fastapi-users) — the NOT NULL columns stay mapped even
where the app no longer writes meaningful values: hashed_password gets
the "supabase-auth" sentinel on mirror insert, token_version is dead
weight from the P1-7 JWT-revocation scheme (kept for rollback safety;
a later migration may drop it).
"""

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Uuid

from app.core.orm import Base
from app.core.timeutil import utc_now


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True)
    email = Column(String(320), nullable=False, unique=True, index=True)
    hashed_password = Column(String(1024), nullable=False, default="supabase-auth")
    is_active = Column(Boolean, nullable=False, default=True)
    is_superuser = Column(Boolean, nullable=False, default=False)
    is_verified = Column(Boolean, nullable=False, default=False)
    # Space for Phase 1+ account fields (stripe_customer_id, plan, etc.)
    display_name = Column(String(120), nullable=True)
    token_version = Column(Integer, default=0, nullable=False, server_default="0")
    created_at = Column(DateTime, default=utc_now, nullable=False)  # python-side: sqlite has no now()

    def __repr__(self):  # pragma: no cover
        return f"<User {self.email} {self.id}>"
