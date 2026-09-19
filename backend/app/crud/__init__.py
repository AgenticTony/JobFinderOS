"""CRUD query helpers for JobFinderOS."""

from datetime import timedelta
from typing import List, Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

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
            # WO-19 B (round-1 finding 5): rows re-evaluated to
            # 'ineligible' after a work-rights change are HIDDEN, not
            # deleted — the row survives so flipping the answer back
            # resurfaces it (decided rows are never re-opened, and an
            # eligibility verdict is not a decision).
            or_(MatchResult.eligibility.is_(None),
                MatchResult.eligibility != "ineligible"),
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
    # joinedload: the list endpoint embeds each application's job
    # (ApplicationResponse.from_orm_application reads .job) — one
    # query, not one-per-row.
    query = db.query(Application).options(
        joinedload(Application.job)
    ).filter(Application.user_id == user_id)
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

# EGRESS 2026-09-11 (Supabase free tier hit 109% of 5GB in 11 days):
# get_stats was the poll-path engine — /pipeline/status polls every
# 60s per open console tab, and every call ran a no-WHERE scan of ALL
# job_postings gate columns (15,515 scans / 9.69M rows in two weeks
# of pg_stat_statements) plus hydrated every kept MatchResult entity
# (20,436 x 80 rows). Both calling endpoints poll, so the expensive
# shapes must be amortized, not per-call.
_POOL_FEED_CACHE: dict = {}
_POOL_FEED_CACHE_MAX_KEYS = 64


def _scoped_feed_counts(db: Session, scope_ctx: dict):
    """(feed_total, feed_last_24h) for one user's scope — the O(pool)
    part of get_stats, cached.

    job_postings rows are NEVER deleted (the stale sweep flips status,
    which the gate does not read), so the pool only changes when a
    hunt writes. Cache key = (scope fingerprint, pool version, hour
    bucket):
      - pool version = (row count, max scraped_at) — a one-row probe;
        any insert moves it (scraped_at carries microseconds).
      - hour bucket bounds the 24h window's staleness: postings age
        out of "last 24h" while the pool idles between hunts, so the
        sliding count is recomputed at most hourly (and immediately on
        any pool write). feed_total stays exact-on-pool-change.
    """
    import json as _json

    from app.services.pipeline import stored_job_in_user_scope

    probe = db.query(
        func.count(JobPosting.id), func.max(JobPosting.scraped_at)
    ).one()
    hour_bucket = utc_now().replace(minute=0, second=0, microsecond=0)
    key = (
        _json.dumps(scope_ctx, sort_keys=True, default=str),
        probe[0], str(probe[1]), hour_bucket,
    )
    cached = _POOL_FEED_CACHE.get(key)
    if cached is not None:
        return cached

    # Attribute access on Row works for the gate (source/remote/
    # location), so the slim column load is a drop-in for the ORM
    # objects without loading descriptions.
    pool_rows = db.query(
        JobPosting.source, JobPosting.remote,
        JobPosting.location, JobPosting.scraped_at,
    ).all()
    feed = [j for j in pool_rows if stored_job_in_user_scope(j, scope_ctx)]
    day_ago = utc_now() - timedelta(hours=24)
    result = (len(feed), sum(1 for j in feed if j.scraped_at >= day_ago))
    if len(_POOL_FEED_CACHE) >= _POOL_FEED_CACHE_MAX_KEYS:
        _POOL_FEED_CACHE.clear()
    _POOL_FEED_CACHE[key] = result
    return result



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

    Perf (egress incident 2026-09-11): both calling endpoints are
    POLLED (pipeline status every 60s per open console tab), so this
    function must not be O(pool) or O(matches) per call. The gate
    scan (slim columns, no description hydration) runs once per
    (scope, pool version, hour) via _scoped_feed_counts, and the
    match tier counts come from one FILTER-aggregate query instead of
    hydrating every MatchResult row.
    """
    from app.services.pipeline import build_scrape_context

    # Stats describe the user's real queue — pipeline-dismissed rows are
    # bookkeeping, not matches, and would inflate every count
    match_q = db.query(MatchResult).filter(
        MatchResult.user_id == user_id, MatchResult.dismissed_reason.is_(None)
    )
    app_q = db.query(Application).filter(Application.user_id == user_id)

    # EGRESS 2026-09-11: match_q.all() hydrated EVERY kept match row
    # (score/tier/rationale/skills — ~KB each) per poll just to derive
    # four integers; one FILTER-aggregate round trip replaces the list.
    (matches_total, matches_excellent,
     matches_good, matches_pending) = db.query(
        func.count(MatchResult.id),
        func.count(MatchResult.id).filter(
            MatchResult.tier == "excellent_match"),
        func.count(MatchResult.id).filter(
            MatchResult.tier == "good_match"),
        func.count(MatchResult.id).filter(
            MatchResult.decision.is_(None)),
    ).filter(
        MatchResult.user_id == user_id,
        MatchResult.dismissed_reason.is_(None),
    ).one()

    def count(query):
        return query.count()

    scope_ctx = build_scrape_context(db, user_id=user_id)
    if scope_ctx is None:
        feed_total = 0
        feed_last_24h = 0
    else:
        feed_total, feed_last_24h = _scoped_feed_counts(db, scope_ctx)

    user_decisions = {
        "approved": match_q.filter(MatchResult.decision == "approved").count(),
        "rejected": match_q.filter(MatchResult.decision == "rejected").count(),
    }

    return {
        "jobs_total": feed_total,
        "jobs_last_24h": feed_last_24h,
        "jobs_matched": matches_total,
        "jobs_approved": user_decisions["approved"],
        "jobs_rejected": user_decisions["rejected"],
        "jobs_applied": app_q.filter(Application.status.in_(["sent", "manual_pending"])).count(),
        "matches_total": matches_total,
        "matches_excellent": matches_excellent,
        "matches_good": matches_good,
        "matches_pending_decision": matches_pending,
        "applications_total": count(app_q),
        "applications_sent": count(app_q.filter(Application.status == "sent")),
        "applications_manual_pending": count(
            app_q.filter(Application.status == "manual_pending")
        ),
        "applications_failed": count(app_q.filter(Application.status == "failed")),
    }

