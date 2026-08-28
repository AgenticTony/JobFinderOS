"""System-level claim locks (WO-04 / D3).

Portable single-row claims — SQLite AND Postgres (no advisory-lock
dialect split). A scheduled hunt claims before running so two worker
processes (or a stray replica) cannot double-fire; a crashed holder
self-heals because claims carry a TTL after which they are stealable.
"""

from sqlalchemy import Column, DateTime, String

from app.core.orm import Base
from app.core.timeutil import utc_now


class SystemLock(Base):
    __tablename__ = "system_locks"

    name = Column(String(50), primary_key=True)
    locked_until = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now,
                        nullable=False)
