"""CRUD query helpers for JobFinderOS."""

from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import Application, JobPosting, MatchResult, ScrapeRun


# ---------------- Jobs ----------------

def list_jobs(
    db: Session,
    status: Optional[str] = None,
    source: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> List[JobPosting]:
    query = db.query(JobPosting)
    if status:
        query = query.filter(JobPosting.status == status)
    if source:
        query = query.filter(JobPosting.source == source)
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(
            or_(
                func.lower(JobPosting.title).like(like),
                func.lower(JobPosting.company).like(like),
            )
        )
    return (
        query.order_by(JobPosting.scraped_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_job(db: Session, job_id: int) -> Optional[JobPosting]:
    return db.query(JobPosting).filter(JobPosting.id == job_id).first()


def delete_job(db: Session, job_id: int) -> bool:
    job = get_job(db, job_id)
    if not job:
        return False
    db.query(MatchResult).filter(MatchResult.job_id == job_id).delete()
    db.query(Application).filter(Application.job_id == job_id).delete()
    db.delete(job)
    db.commit()
    return True


# ---------------- Matches ----------------

def list_matches(
    db: Session,
    tier: Optional[str] = None,
    recommendation: Optional[str] = None,
    min_score: int = 0,
    pending_only: bool = False,
    limit: int = 100,
    offset: int = 0,
) -> List[MatchResult]:
    query = db.query(MatchResult).join(JobPosting, MatchResult.job_id == JobPosting.id)
    if tier:
        query = query.filter(MatchResult.tier == tier)
    if recommendation:
        query = query.filter(MatchResult.recommendation == recommendation)
    if min_score:
        query = query.filter(MatchResult.score >= min_score)
    if pending_only:
        query = query.filter(MatchResult.decision.is_(None))
    return (
        query.order_by(MatchResult.score.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_match(db: Session, match_id: int) -> Optional[MatchResult]:
    return db.query(MatchResult).filter(MatchResult.id == match_id).first()


def set_match_decision(db: Session, match: MatchResult, decision: str) -> MatchResult:
    from datetime import datetime

    match.decision = decision
    match.decided_at = datetime.utcnow()
    job = get_job(db, match.job_id)
    if job:
        job.status = "approved" if decision == "approved" else "rejected"
        db.add(job)
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


# ---------------- Applications ----------------

def list_applications(db: Session, limit: int = 100, offset: int = 0) -> List[Application]:
    return (
        db.query(Application)
        .order_by(Application.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_application(db: Session, application_id: int) -> Optional[Application]:
    return db.query(Application).filter(Application.id == application_id).first()


# ---------------- Scrape runs ----------------

def list_scrape_runs(db: Session, limit: int = 20) -> List[ScrapeRun]:
    return (
        db.query(ScrapeRun)
        .order_by(ScrapeRun.started_at.desc())
        .limit(limit)
        .all()
    )


# ---------------- Stats ----------------

def get_stats(db: Session) -> dict:
    def count(model, **filters):
        query = db.query(model)
        for col, val in filters.items():
            query = query.filter(getattr(model, col) == val)
        return query.count()

    day_ago = datetime.utcnow() - timedelta(hours=24)
    matches = db.query(MatchResult).all()
    return {
        "jobs_total": count(JobPosting),
        # Measured from the jobs table (not run reports) so it can never
        # exceed jobs_total, even after country switches or cleanups.
        "jobs_last_24h": db.query(JobPosting).filter(JobPosting.scraped_at >= day_ago).count(),
        "jobs_new": count(JobPosting, status="new"),
        "jobs_matched": count(JobPosting, status="matched"),
        "jobs_approved": count(JobPosting, status="approved"),
        "jobs_rejected": count(JobPosting, status="rejected"),
        "jobs_dismissed": count(JobPosting, status="dismissed"),
        "jobs_applied": count(JobPosting, status="applied"),
        "matches_total": len(matches),
        "matches_excellent": sum(1 for m in matches if m.tier == "excellent_match"),
        "matches_good": sum(1 for m in matches if m.tier == "good_match"),
        "matches_pending_decision": sum(1 for m in matches if m.decision is None),
        "applications_total": count(Application),
        "applications_sent": count(Application, status="sent"),
        "applications_manual_pending": count(Application, status="manual_pending"),
        "applications_failed": count(Application, status="failed"),
    }
