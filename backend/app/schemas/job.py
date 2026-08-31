"""Job posting schemas for JobFinderOS."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, EmailStr


class JobCreate(BaseModel):
    """Manual job entry (bypasses scrapers)."""
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    url: str
    description: Optional[str] = None
    employment_type: Optional[str] = None
    salary: Optional[str] = None
    # P1-3: this becomes application.target_email — a future EMAIL SEND
    # TARGET. Accepted verbatim before ("not-an-email-at-all <<>>" lived
    # in the DB), so validate at the boundary: 422 before anything runs.
    application_email: Optional[EmailStr] = None
    application_url: Optional[str] = None
    tags: List[str] = []
    remote: bool = False


class JobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source: str
    source_id: Optional[str] = None
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote: bool = False
    url: str
    employment_type: Optional[str] = None
    salary: Optional[str] = None
    status: str
    application_email: Optional[str] = None
    application_url: Optional[str] = None
    published_at: Optional[datetime] = None
    scraped_at: datetime
    tags: List[str] = []

    @classmethod
    def from_orm_job(cls, job) -> "JobResponse":
        from app.schemas.common import parse_json_list
        return cls(
            id=job.id,
            source=job.source,
            source_id=job.source_id,
            title=job.title,
            company=job.company,
            location=job.location,
            remote=bool(job.remote),
            url=job.url,
            employment_type=job.employment_type,
            salary=job.salary,
            status=job.status,
            application_email=job.application_email,
            application_url=job.application_url,
            published_at=job.published_at,
            scraped_at=job.scraped_at,
            tags=parse_json_list(job.tags),
        )


class JobDetailResponse(JobResponse):
    description: Optional[str] = None

    @classmethod
    def from_orm_job(cls, job) -> "JobDetailResponse":
        base = JobResponse.from_orm_job(job)
        return cls(**base.model_dump(), description=job.description)


# JobStatusUpdate was removed with PATCH /jobs/{id}/status (P1-4): it
# mutated the SHARED job_postings.status for every user at once. Per-user
# decisions live on match_results (decision / dismissed_reason).
