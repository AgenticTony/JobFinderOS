"""CRUD query helpers for JobFinderOS."""

from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.models import (
    Application,
    ApplicationDraft,
    JobPosting,
    MatchResult,
    ScrapeRun,
)

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


def delete_job(db: Session, job_id: int, *, user_id) -> bool:
    """Remove a job from ONE user's world.

    job_postings is a shared pool: the row is only physically deleted when
    no other user still references it. Otherwise the caller's own match and
    application rows go and the shared posting stays, so a delete can never
    dangle another tenant's foreign keys (MatchResult.job_id is NOT NULL).
    """
    job = get_job(db, job_id)
    if not job:
        return False
    # Children first (P0-2): applications reference drafts AND matches
    # (draft_id, match_id), drafts reference matches (match_id). Those FKs
    # are NOT DEFERRABLE with no ON DELETE action, so deleting matches
    # before their dependents raised IntegrityError on Postgres —
    # DELETE /jobs/{id} 500'd for any drafted or applied job.
    db.query(Application).filter(
        Application.job_id == job_id, Application.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(ApplicationDraft).filter(
        ApplicationDraft.job_id == job_id, ApplicationDraft.user_id == user_id
    ).delete(synchronize_session=False)
    db.query(MatchResult).filter(
        MatchResult.job_id == job_id, MatchResult.user_id == user_id
    ).delete(synchronize_session=False)
    db.flush()  # make this user's removals visible to the reference checks

    still_referenced = (
        db.query(MatchResult.id).filter(MatchResult.job_id == job_id).first()
        or db.query(Application.id).filter(Application.job_id == job_id).first()
        or db.query(ApplicationDraft.id).filter(ApplicationDraft.job_id == job_id).first()
    )
    if not still_referenced:
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
    *,
    user_id,
) -> List[MatchResult]:
    query = (
        db.query(MatchResult)
        .join(JobPosting, MatchResult.job_id == JobPosting.id)
        .filter(
            MatchResult.user_id == user_id,
            # Pipeline-dismissed rows exist only to stop re-evaluation and
            # keep an audit trail — they are never part of the user's queue
            MatchResult.dismissed_reason.is_(None),
        )
    )
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

    match.decision = decision
    match.decided_at = utc_now()
    # NOTE: job.status is NOT touched — approval/rejection is per-user state
    # that lives here in match_results.decision. Writing it onto the shared
    # job row leaked one user's decision to every other user.
    db.add(match)
    db.commit()
    db.refresh(match)
    return match


# ---------------- Applications ----------------

def list_applications(
    db: Session, limit: int = 100, offset: int = 0, *, user_id
) -> List[Application]:
    query = db.query(Application).filter(Application.user_id == user_id)
    return (
        query
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

def get_stats(db: Session, *, user_id) -> dict:
    """Dashboard stats for ONE user.

    Per-user derivations: decision/approval state comes from match_results,
    applied state from applications — job.status carries no user state.
    (job_* counts describe the shared scraped pool, which is not per-user.)
    """
    # Stats describe the user's real queue — pipeline-dismissed rows are
    # bookkeeping, not matches, and would inflate every count
    match_q = db.query(MatchResult).filter(
        MatchResult.user_id == user_id, MatchResult.dismissed_reason.is_(None)
    )
    job_q = db.query(JobPosting)
    app_q = db.query(Application).filter(Application.user_id == user_id)

    matches = match_q.all()

    def count(query):
        return query.count()

    user_decisions = {
        "approved": match_q.filter(MatchResult.decision == "approved").count(),
        "rejected": match_q.filter(MatchResult.decision == "rejected").count(),
    }

    return {
        "jobs_total": count(job_q),
        "jobs_last_24h": count(
            job_q.filter(JobPosting.scraped_at >= utc_now() - timedelta(hours=24))
        ),
        "jobs_new": count(job_q.filter(JobPosting.status == "new")),
        "jobs_matched": count(job_q.filter(JobPosting.status == "matched")),
        "jobs_approved": user_decisions["approved"],
        "jobs_rejected": user_decisions["rejected"],
        "jobs_dismissed": count(job_q.filter(JobPosting.status == "dismissed")),
        "jobs_applied": app_q.filter(Application.status.in_(["sent", "manual_pending"])).count(),
        "matches_total": len(matches),
        "matches_excellent": sum(1 for m in matches if m.tier == "excellent_match"),
        "matches_good": sum(1 for m in matches if m.tier == "good_match"),
        "matches_pending_decision": sum(1 for m in matches if m.decision is None),
        "applications_total": count(app_q),
        "applications_sent": count(app_q.filter(Application.status == "sent")),
        "applications_manual_pending": count(
            app_q.filter(Application.status == "manual_pending")
        ),
        "applications_failed": count(app_q.filter(Application.status == "failed")),
    }

