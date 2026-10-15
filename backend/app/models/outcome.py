"""WO-20 outcome layer models — proposals (propose-then-confirm) and
the per-user Gmail scan watermark.

Nothing in reply detection or the quiet sweep writes `applications`
directly: both file an OutcomeProposal citing its evidence, and only an
explicit user approval (decision='approved') applies it. A wrong write
corrupts the stats the landing page will publish — same spirit as
invariant #1 (nothing outbound without approval), applied to the inbox.
"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)

from app.core.orm import Base
from app.core.timeutil import utc_now


class OutcomeProposal(Base):
    """One proposed outcome change, awaiting the user's decision."""

    __tablename__ = "outcome_proposals"
    # Re-scan dedupe at the DB level: ONE proposal per (user, message) —
    # an email names at most one application, ever (review round 2,
    # finding 6 replaced the per-application key, which let a later scan
    # re-file the same message against a second application after the
    # first closed). Sweep proposals carry no message id (NULL) and are
    # unaffected — multiple NULLs are distinct on both backends.
    __table_args__ = (
        Index(
            "uq_outcome_proposals_user_message",
            "user_id",
            "evidence_message_id",
            unique=True,
            sqlite_where=text("evidence_message_id IS NOT NULL"),
            postgresql_where=text("evidence_message_id IS NOT NULL"),
        ),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Uuid, ForeignKey("users.id"), nullable=False, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"), nullable=False, index=True)

    proposed_status = Column(String(20), nullable=False)
    source = Column(String(20), nullable=False)  # reply_detection | sweep

    # Evidence pointer + citation context. PII rule: never store the
    # message BODY — the classification and where to find it only.
    evidence_message_id = Column(String(255), nullable=True)
    evidence_from = Column(String(255), nullable=True)
    evidence_subject = Column(String(500), nullable=True)
    # When the employer's message actually arrived. resolved_at is set
    # from this on approval, so time-to-first-response measures the
    # reply — not the moment we happened to scan the inbox.
    evidence_received_at = Column(DateTime, nullable=True)
    reason = Column(Text, nullable=True)  # why classified (matched phrase)

    # NULL = pending | approved | dismissed. Only approved applies.
    decision = Column(String(20), nullable=True, index=True)
    decided_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=utc_now, nullable=False)

    def __repr__(self):
        return (
            f"<OutcomeProposal app={self.application_id} "
            f"{self.proposed_status} src={self.source} {self.decision or 'pending'}>"
        )


class GmailScanState(Base):
    """Per-user incremental scan watermark — a processed message is
    never reprocessed."""

    __tablename__ = "gmail_scan_state"

    user_id = Column(Uuid, ForeignKey("users.id"), primary_key=True)
    # Scan watermark (review round 3): "e:{epoch}" = incremental —
    # mail at or before the epoch is fully scanned; "b:{lo}:{hi}" =
    # backfill — (lo, hi) still overflowed the per-scan cap, hi sits at
    # the oldest processed arrival +1s, and the next scan works the
    # remainder top-down. Legacy "YYYY-MM-DD" cursors parse as
    # incremental windows with the old one-day overlap.
    scan_cursor = Column(String(64), nullable=True)
    last_scan_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    def __repr__(self):
        return f"<GmailScanState user={self.user_id} after={self.scan_cursor}>"
