"""Application + draft schemas for JobFinderOS."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class DraftSubmitRequest(BaseModel):
    method: str = "email"  # email | browser | manual


class DraftUpdateRequest(BaseModel):
    cover_letter: Optional[str] = None
    tailored_cv: Optional[str] = None


class DraftResponse(BaseModel):
    id: int
    job_id: int
    match_id: Optional[int] = None
    cover_letter: Optional[str] = None
    tailored_cv: Optional[str] = None
    changes_summary: List[str] = []
    status: str  # drafting | ready | submitted | failed
    error: Optional[str] = None
    # WO-01 fabrication guard: ADVISORY findings for the review UI
    # (technology-class; high-confidence ones never reach here — they
    # drove regeneration or a block before the draft went ready)
    fabrication_findings: List[dict] = []
    fabrication_retries: int = 0
    fabrication_blocked: bool = False
    created_at: datetime
    updated_at: datetime
    job: Optional[dict] = None

    @classmethod
    def from_orm_draft(cls, d) -> "DraftResponse":
        from app.schemas.common import parse_json_list
        from app.schemas.job import JobResponse

        return cls(
            id=d.id,
            job_id=d.job_id,
            match_id=d.match_id,
            cover_letter=d.cover_letter,
            tailored_cv=d.tailored_cv,
            changes_summary=parse_json_list(d.changes_summary),
            status=d.status,
            error=d.error,
            fabrication_findings=parse_json_list(getattr(d, "fabrication_findings", None), default=[]) or [],
            fabrication_retries=getattr(d, "fabrication_retries", 0) or 0,
            fabrication_blocked=bool(getattr(d, "fabrication_blocked", False)),
            created_at=d.created_at,
            updated_at=d.updated_at,
            job=JobResponse.from_orm_job(d.job).model_dump() if d.job else None,
        )


class ApplicationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_id: int
    match_id: Optional[int] = None
    draft_id: Optional[int] = None
    method: str
    status: str  # queued, sent, failed, manual_pending
    subject: Optional[str] = None
    body: Optional[str] = None
    target_email: Optional[str] = None
    apply_url: Optional[str] = None
    sent_at: Optional[datetime] = None
    error: Optional[str] = None
    created_at: datetime
