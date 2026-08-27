"""Match result schemas for JobFinderOS."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict

from app.schemas.job import JobResponse


class MatchDecision(BaseModel):
    decision: str  # approved | rejected


class MatchResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    score: int
    tier: str  # excellent_match, good_match, stretch, poor_match
    reasoning: Optional[str] = None
    matched_skills: List[str] = []
    missing_skills: List[str] = []
    transferable_skills: List[str] = []
    recommendation: Optional[str] = None  # apply, maybe, skip
    confidence: Optional[str] = None
    decision: Optional[str] = None
    decided_at: Optional[datetime] = None
    created_at: datetime

    @classmethod
    def from_orm_match(cls, m) -> "MatchResponse":
        from app.schemas.common import parse_json_list
        return cls(
            id=m.id,
            job_id=m.job_id,
            score=m.score,
            tier=m.tier,
            reasoning=m.reasoning,
            matched_skills=parse_json_list(m.matched_skills),
            missing_skills=parse_json_list(m.missing_skills),
            transferable_skills=parse_json_list(m.transferable_skills),
            recommendation=m.recommendation,
            confidence=m.confidence,
            decision=m.decision,
            decided_at=m.decided_at,
            created_at=m.created_at,
        )


class MatchWithJobResponse(MatchResponse):
    job: Optional[JobResponse] = None

    @classmethod
    def from_orm_match(cls, m) -> "MatchWithJobResponse":
        base = MatchResponse.from_orm_match(m)
        job = JobResponse.from_orm_job(m.job) if m.job else None
        return cls(**base.model_dump(), job=job)
