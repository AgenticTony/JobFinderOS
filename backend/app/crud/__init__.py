"""CRUD query helpers for JobFinderOS."""

from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.timeutil import utc_now
from app.models import (
    Application,
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


# delete_job is GONE (2026-08-31) with its only caller, DELETE /jobs/{id}:
# the "unreferenced" branch let ANY authenticated user permanently delete
# shared-pool postings nobody had matched yet (external verification
# pass 2, live-proven cross-tenant). Per-user removal of a job is
# match_results.dismissed_reason, which is where it lives.


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
    """Dashboard stats for ONE user — the hunt-pulse funnel.

    PERSONAL FUNNEL (owner decision 2026-09-01): every count is the
    user's own. Hunted / +N-in-24h count jobs stored in THIS user's
    scope (the same stored_job_in_user_scope predicate matching
    applies — no second location policy to drift). Deliberately NOT
    bounded by the join date: the first match run scores the
    pre-existing pool by design (instant day-one value), so bounding
    Hunted but not Matched would invert the funnel — Matched greater
    than Hunted on day one. A user with no onboarded profile has no
    scope, hence no feed: zeros, never the shared pool.

    Matched is this user's kept match rows (jobs ranked against THEIR
    CV); job.status carries no user state, so it is not read at all —
    jobs_new/jobs_dismissed were removed with it (they derived from
    the shared status column and moved with other users' activity).

    Perf: only the location gate's columns are loaded (no description
    Text hydration). job_postings rows are NEVER deleted — the stale
    sweep flips status to 'dismissed' only — so this scan must stay
    slim as the pool grows; both calling endpoints poll it.
    """
    from app.services.pipeline import build_scrape_context, stored_job_in_user_scope

    # Stats describe the user's real queue — pipeline-dismissed rows are
    # bookkeeping, not matches, and would inflate every count
    match_q = db.query(MatchResult).filter(
        MatchResult.user_id == user_id, MatchResult.dismissed_reason.is_(None)
    )
    app_q = db.query(Application).filter(Application.user_id == user_id)

    matches = match_q.all()

    def count(query):
        return query.count()

    scope_ctx = build_scrape_context(db, user_id=user_id)
    if scope_ctx is None:
        feed_total = 0
        feed_last_24h = 0
    else:
        # Attribute access on Row works for the gate (source/remote/
        # location), so the slim column load is a drop-in for the ORM
        # objects without loading descriptions.
        pool_rows = db.query(
            JobPosting.source, JobPosting.remote, JobPosting.location, JobPosting.scraped_at
        ).all()
        feed = [j for j in pool_rows if stored_job_in_user_scope(j, scope_ctx)]
        feed_total = len(feed)
        day_ago = utc_now() - timedelta(hours=24)
        feed_last_24h = sum(1 for j in feed if j.scraped_at >= day_ago)

    user_decisions = {
        "approved": match_q.filter(MatchResult.decision == "approved").count(),
        "rejected": match_q.filter(MatchResult.decision == "rejected").count(),
    }

    return {
        "jobs_total": feed_total,
        "jobs_last_24h": feed_last_24h,
        "jobs_matched": len(matches),
        "jobs_approved": user_decisions["approved"],
        "jobs_rejected": user_decisions["rejected"],
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

