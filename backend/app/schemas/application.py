"""Application + draft schemas for JobFinderOS."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class DraftSubmitRequest(BaseModel):
    method: str = "email"  # email | browser | manual


class OutcomeUpdateRequest(BaseModel):
    """WO-20 manual outcome marker — the user's own word about what the
    employer did. Manual outcomes outrank detected ones (never overwritten
    by an approved proposal)."""
    outcome_status: str  # outcome_service.OUTCOME_STATUSES
    note: Optional[str] = None


class DraftUpdateRequest(BaseModel):
    cover_letter: Optional[str] = None
    tailored_cv: Optional[str] = None


class DraftAttestRequest(BaseModel):
    """WO-23: per-claim 'This is true — keep it' on a blocked draft."""
    claim: str
    save_to_profile: bool = True  # default on per the WO flow
    # Round-2 N1: the EXPLICIT fact text to save to the profile (the UI
    # displays it in full next to the opt-in). Absent -> nothing is
    # saved: the backend never derives guard truth the user didn't see
    # and confirm — a sentence's unconfirmed claims ("team of 12") must
    # not ride in on one confirmed atom ("40%").
    profile_fact: Optional[str] = None
    # Round 5: the finding's sentence — pins WHICH flagged use of a
    # repeated value the user confirmed. Absent: the first matching use
    # (the value's other uses stay flagged).
    context: Optional[str] = None


class DraftResponse(BaseModel):
    id: int
    job_id: int
    match_id: Optional[int] = None
    cover_letter: Optional[str] = None
    tailored_cv: Optional[str] = None
    changes_summary: List[str] = []
    # queued (WO-31: waiting in ai_jobs) | drafting (legacy inline) |
    # ready | sending (transient submit claim) | submitted | failed
    status: str
    error: Optional[str] = None
    # WO-31: the draft's ACTIVE ai_job (tailor or recheck), or None —
    # {id, kind, status: queued|running, position, eta_minutes,
    # paused_until}. Drives the honest "you're in a queue" card copy.
    queue: Optional[dict] = None
    # WO-01 fabrication guard findings. Since WO-23 the column carries
    # tier, and a BLOCKED draft persists its high findings too (the
    # recovery UI's content). Consumers showing the advisory panel must
    # filter tier == 'advisory' — high items are resolved via
    # fix-in-text, attestation, or regeneration, never shown as advisory.
    fabrication_findings: List[dict] = []
    fabrication_retries: int = 0
    fabrication_blocked: bool = False
    # WO-23 recovery record: attested claims (never cleared) + when the
    # draft recovered to ready. fabrication_blocked stays true through
    # recovery — it is the raw fabrication-rate data.
    fabrication_attested: List[dict] = []
    fabrication_resolved_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    job: Optional[dict] = None

    @classmethod
    def from_orm_draft(cls, d, queue: Optional[dict] = None) -> "DraftResponse":
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
            queue=queue,
            fabrication_findings=parse_json_list(getattr(d, "fabrication_findings", None), default=[]) or [],
            fabrication_retries=getattr(d, "fabrication_retries", 0) or 0,
            fabrication_blocked=bool(getattr(d, "fabrication_blocked", False)),
            fabrication_attested=parse_json_list(getattr(d, "fabrication_attested", None), default=[]) or [],
            fabrication_resolved_at=getattr(d, "fabrication_resolved_at", None),
            created_at=d.created_at,
            updated_at=d.updated_at,
            job=JobResponse.from_orm_job(d.job).model_dump() if d.job else None,
        )


class ApplicationResponse(BaseModel):
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
    # WO-20 outcome layer — the employer-side lifecycle. Parallel to, and
    # never mixed with, send-side `status` above.
    outcome_status: Optional[str] = None
    outcome_source: Optional[str] = None  # manual | reply_detection | sweep
    outcome_note: Optional[str] = None
    resolved_at: Optional[datetime] = None
    follow_up_of_id: Optional[int] = None
    created_at: datetime
    # EGRESS 2026-09-11: the Sent page used to join job titles
    # client-side by WALKING the whole /jobs/ pool 500 rows at a time
    # per fetch (~900 full rows per refresh; 2,081 walks in two
    # weeks). The response now embeds the same JobResponse the walk
    # was reconstructing — same shape DraftResponse already ships.
    job: Optional[dict] = None

    @classmethod
    def from_orm_application(cls, a) -> "ApplicationResponse":
        from app.schemas.job import JobResponse

        return cls(
            id=a.id,
            job_id=a.job_id,
            match_id=a.match_id,
            draft_id=a.draft_id,
            method=a.method,
            status=a.status,
            subject=a.subject,
            body=a.body,
            target_email=a.target_email,
            apply_url=a.apply_url,
            sent_at=a.sent_at,
            error=a.error,
            outcome_status=getattr(a, "outcome_status", None),
            outcome_source=getattr(a, "outcome_source", None),
            outcome_note=getattr(a, "outcome_note", None),
            resolved_at=getattr(a, "resolved_at", None),
            follow_up_of_id=getattr(a, "follow_up_of_id", None),
            created_at=a.created_at,
            job=JobResponse.from_orm_job(a.job).model_dump() if a.job else None,
        )
