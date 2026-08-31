"""Jobs API — scraped job postings list, detail, manual add, delete."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_authenticated_user
from app.core.database import get_db
from app.core.dedupe import dedupe_key_for
from app.core.ratelimit import enforce
from app.crud import delete_job, get_job, list_jobs
from app.models import JobPosting, User
from app.schemas.common import dump_json_list
from app.schemas.job import JobCreate, JobDetailResponse, JobResponse

router = APIRouter()


@router.get("/", response_model=list[JobResponse])
async def get_jobs(
    user: User = Depends(get_authenticated_user),
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return [JobResponse.from_orm_job(j) for j in list_jobs(db, status, source, q, limit, offset)]


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_detail(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse.from_orm_job(job)


@router.post("/", response_model=JobDetailResponse, status_code=201)
async def create_manual_job(
    payload: JobCreate, db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    """Add a job manually (e.g. pasted from a site we don't scrape yet)."""
    # P1-3: every attempt counts — the burst is the attack, valid or not
    enforce(user.id, "job_create")
    job = JobPosting(
        source="manual",
        title=payload.title,
        company=payload.company,
        location=payload.location,
        remote=1 if payload.remote else 0,
        url=payload.url,
        description=payload.description,
        employment_type=payload.employment_type,
        salary=payload.salary,
        tags=dump_json_list(payload.tags),
        application_email=payload.application_email,
        application_url=payload.application_url,
    )
    job.dedupe_key = dedupe_key_for(job.title, job.company, job.location)
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobDetailResponse.from_orm_job(job)


# P1-4: PATCH /{job_id}/status is GONE (2026-08-30). It wrote
# job_postings.status — a SHARED row — for every user at once: one
# account's "dismissed" removed the job from EVERY user's matching queue
# (matcher_service filters job.status != "dismissed" pool-wide) and the
# re-queue guard was unscoped. The frontend never called it, and per-user
# dismissal already lives on match_results.dismissed_reason.


@router.delete("/{job_id}", status_code=204)
async def remove_job(
    job_id: int, db: Session = Depends(get_db), user: User = Depends(get_authenticated_user)
):
    if not delete_job(db, job_id, user_id=user.id):
        raise HTTPException(status_code=404, detail="Job not found")
