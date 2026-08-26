"""Jobs API — scraped job postings list, detail, manual add, delete."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.dedupe import dedupe_key_for
from app.crud import delete_job, get_job, list_jobs
from app.models import JobPosting, MatchResult
from app.schemas.common import dump_json_list
from app.schemas.job import JobCreate, JobDetailResponse, JobResponse, JobStatusUpdate

router = APIRouter()

VALID_STATUSES = {"new", "matched", "approved", "rejected", "dismissed", "applied"}


@router.get("/", response_model=list[JobResponse])
async def get_jobs(
    status: str | None = None,
    source: str | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    return [JobResponse.from_orm_job(j) for j in list_jobs(db, status, source, q, limit, offset)]


@router.get("/{job_id}", response_model=JobDetailResponse)
async def get_job_detail(job_id: int, db: Session = Depends(get_db)):
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse.from_orm_job(job)


@router.post("/", response_model=JobDetailResponse, status_code=201)
async def create_manual_job(payload: JobCreate, db: Session = Depends(get_db)):
    """Add a job manually (e.g. pasted from a site we don't scrape yet)."""
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


@router.patch("/{job_id}/status", response_model=JobResponse)
async def update_job_status(
    job_id: int, payload: JobStatusUpdate, db: Session = Depends(get_db)
):
    if payload.status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status. Must be one of {sorted(VALID_STATUSES)}",
        )
    job = get_job(db, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if payload.status == "new" and db.query(MatchResult.id).filter(MatchResult.job_id == job.id).first():
        # Re-queuing a matched job collides with UNIQUE(match_results.job_id)
        raise HTTPException(
            status_code=400,
            detail="Job already has a match — cannot re-queue as new (delete its match first)",
        )
    job.status = payload.status
    db.add(job)
    db.commit()
    db.refresh(job)
    return JobResponse.from_orm_job(job)


@router.delete("/{job_id}", status_code=204)
async def remove_job(job_id: int, db: Session = Depends(get_db)):
    if not delete_job(db, job_id):
        raise HTTPException(status_code=404, detail="Job not found")
