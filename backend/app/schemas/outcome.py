"""WO-20 outcome schemas — proposals, decisions, scans."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class OutcomeProposalResponse(BaseModel):
    id: int
    application_id: int
    job_id: Optional[int] = None  # denormalized for the review card
    proposed_status: str
    source: str  # reply_detection | sweep
    evidence_message_id: Optional[str] = None
    evidence_from: Optional[str] = None
    evidence_subject: Optional[str] = None
    evidence_received_at: Optional[datetime] = None
    reason: Optional[str] = None
    decision: Optional[str] = None  # NULL = pending
    decided_at: Optional[datetime] = None
    created_at: datetime

    @classmethod
    def from_orm_proposal(cls, p, job_id: Optional[int] = None) -> "OutcomeProposalResponse":
        return cls(
            id=p.id,
            application_id=p.application_id,
            job_id=job_id,
            proposed_status=p.proposed_status,
            source=p.source,
            evidence_message_id=p.evidence_message_id,
            evidence_from=p.evidence_from,
            evidence_subject=p.evidence_subject,
            evidence_received_at=p.evidence_received_at,
            reason=p.reason,
            decision=p.decision,
            decided_at=p.decided_at,
            created_at=p.created_at,
        )


class OutcomeProposalDecisionRequest(BaseModel):
    decision: str  # approved | dismissed
