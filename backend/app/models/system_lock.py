"""System-level claim locks (WO-04 / D3).

Portable single-row claims — SQLite AND Postgres (no advisory-lock
dialect split). A scheduled hunt claims before running so two worker
processes (or a stray replica) cannot double-fire; a crashed holder
self-heals because claims carry a TTL after which they are stealable.

PIPE-18: claims are OWNED. Each claim mints an owner_token; release is
a conditional UPDATE keyed on it, so a holder whose TTL was stolen can
never release the stealer's claim (which used to open two concurrent
hunts).
"""

from sqlalchemy import Column, DateTime, String

from app.core.orm import Base
from app.core.timeutil import utc_now


class SystemLock(Base):
    __tablename__ = "system_locks"

    name = Column(String(50), primary_key=True)
    locked_until = Column(DateTime, nullable=True)
    # PIPE-18: uuid4 hex of the claiming process's hunt; NULL when free.
    # Release/renew match on it — the lock is only ever cleared by its
    # owner (or by TTL expiry, which is a steal, not a release).
    owner_token = Column(String(64), nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now,
                        nullable=False)
