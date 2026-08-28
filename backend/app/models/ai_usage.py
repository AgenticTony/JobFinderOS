"""Per-call AI usage + cost rows — the observability table.

One row per AI call: cost accounting (the 1.9x price-blindness class),
price-drift detection (recorded usage vs billed), and the residency
audit trail (endpoint hostname + model + timestamp + request id, per
Mistral's documented regional-inference audit requirements — the same
schema serves cost accounting and residency proof).
"""

from sqlalchemy import Column, DateTime, Integer, String, Text, Uuid

from app.core.orm import Base
from app.core.timeutil import utc_now


class AIUsage(Base):
    __tablename__ = "ai_usage"

    id = Column(Integer, primary_key=True, index=True)
    # Nullable: system-context calls (re-score script) have no user
    user_id = Column(Uuid, nullable=True, index=True)
    kind = Column(String(20), nullable=False, index=True)  # match|tailor|judge|extract|suggest
    model = Column(String(50), nullable=False)
    # The residency-audit field: which ENDPOINT served this call
    endpoint = Column(String(200), nullable=True)
    request_id = Column(String(100), nullable=True)
    prompt_tokens = Column(Integer, nullable=False, default=0)
    cached_tokens = Column(Integer, nullable=False, default=0)
    completion_tokens = Column(Integer, nullable=False, default=0)
    cost_usd = Column(Integer, nullable=True)  # micro-dollars, computed at write
    detail = Column(Text, nullable=True)  # e.g. error note
    created_at = Column(DateTime, default=utc_now, nullable=False, index=True)
