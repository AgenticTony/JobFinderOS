"""The worker process — scheduler + hunts, OUT of the API (WO-04/D3).

Two API replicas used to mean two in-process schedulers racing the
same hunt. Now: the API lifespan never starts a scheduler in
production (ENABLE_SCHEDULER defaults false), and THIS entrypoint is
the only thing that hunts:

    python -m app.worker

Every scheduled cycle claims the DB hunt lock first — portable
(SQLite + Postgres), TTL-stealable (a crashed holder self-heals),
always released BY ITS OWNER (PIPE-18). A double-started worker skips
harmlessly.
"""

import logging
import uuid

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.services.ai_service import current_user_id

logger = logging.getLogger(__name__)

# PIPE-18 — the claim TTL is SIZED, not guessed. The old fixed
# CLAIM_TTL_MINUTES=45 covered the scrape phase + at most SIX users'
# matching (MATCH_TIME_BUDGET_SECONDS=420 each), while a scheduled hunt
# matches EVERY onboarded user in one claimed cycle: at N users the
# holder overran its own TTL, a second worker stole the claim, and the
# overrunner's unconditional release then freed the STEALER's claim —
# two concurrent hunts double-scoring the shared pool.
CLAIM_TTL_FLOOR_MINUTES = 45  # scrape phase + small deployments; keeps
#                              the historical minimum release cadence
SCRAPE_PHASE_ALLOWANCE_MINUTES = 15  # union contexts x sources, with retries
CLAIM_TTL_SAFETY_FACTOR = 1.25  # headroom over the computed worst case


def _onboarded_user_count(db) -> int:
    """Users this hunt cycle will match (the worker's own enumeration)."""
    from app.models import Profile

    try:
        return (
            db.query(Profile.user_id)
            .filter(Profile.country.isnot(None), Profile.user_id.isnot(None))
            .distinct()
            .count()
        )
    except Exception:  # noqa: BLE001 — sizing must never break claiming
        return 0


def compute_claim_ttl_minutes(user_count: int) -> int:
    """Worst-case hunt budget: the scrape phase plus ONE matching time
    budget per onboarded user, with headroom, floored at the historical
    45 minutes.

    MATCH_TIME_BUDGET_SECONDS is the binding per-user cost — it is the
    hard wall-clock stop of a matching run. MAX_JOBS_PER_MATCH_RUN (the
    200-evaluation spend cap) cannot exceed it in wall time (200 evals
    x up to 3 samples at ~5-10s each is stopped by the 420s budget long
    before the cap), so sizing on the time budget covers both.
    """
    import math

    per_user_minutes = math.ceil(settings.MATCH_TIME_BUDGET_SECONDS / 60)
    worst = SCRAPE_PHASE_ALLOWANCE_MINUTES + max(int(user_count), 0) * per_user_minutes
    return max(CLAIM_TTL_FLOOR_MINUTES, math.ceil(worst * CLAIM_TTL_SAFETY_FACTOR))


def _claim_ttl_minutes(db) -> int:
    """Effective TTL: the ops override, else the computed worst case."""
    return settings.HUNT_CLAIM_TTL_MINUTES or compute_claim_ttl_minutes(
        _onboarded_user_count(db)
    )


def claim_hunt(db):
    """Claim the hunt lock. Returns the claim's OWNER TOKEN (truthy) when
    this process runs the cycle, None when someone else holds it (skip,
    don't error). Stale claims (crashed holder past TTL) are stealable —
    stealing mints a NEW owner token, so the overrunner cannot release
    the stealer's claim.

    ATOMIC (review r2): one conditional UPDATE whose rowcount is the
    verdict — the previous SELECT->check->UPDATE had no serialization
    and a seeded 8-thread race produced 8/8 winners. Portable: the
    single-statement UPDATE is atomic on both SQLite and Postgres.
    """
    import datetime

    from sqlalchemy import or_, update

    from app.core.timeutil import utc_now
    from app.models import SystemLock

    now = utc_now()
    new_until = now + datetime.timedelta(minutes=_claim_ttl_minutes(db))
    owner_token = uuid.uuid4().hex

    result = db.execute(
        update(SystemLock)
        .where(
            SystemLock.name == "hunt",
            or_(SystemLock.locked_until.is_(None),
                SystemLock.locked_until <= now),
        )
        .values(locked_until=new_until, owner_token=owner_token)
    )
    db.commit()
    if result.rowcount == 1:
        return owner_token

    # rowcount 0: either held (None) or the row does not exist yet —
    # first-ever claim via INSERT; the PK makes a second inserter lose
    db.rollback()
    db.add(SystemLock(name="hunt", locked_until=new_until, owner_token=owner_token))
    try:
        db.commit()
        return owner_token
    except Exception:  # noqa: BLE001 — PK collision = another process claimed first
        db.rollback()
        return None


def release_hunt(db, owner_token) -> bool:
    """Release OUR claim — a conditional UPDATE keyed on the owner
    token (PIPE-18). A holder whose TTL was stolen releases NOTHING:
    the stealer owns the claim now, and clearing it would put a second
    hunt in flight. Idempotent for the true owner — safe on the
    crashed-after-release path. Returns True when this call freed it.
    """
    from sqlalchemy import update

    from app.models import SystemLock

    result = db.execute(
        update(SystemLock)
        .where(
            SystemLock.name == "hunt",
            SystemLock.owner_token == owner_token,
            SystemLock.locked_until.isnot(None),
        )
        .values(locked_until=None, owner_token=None)
    )
    db.commit()
    return result.rowcount == 1


def renew_hunt(db, owner_token) -> bool:
    """Heartbeat: extend OUR claim's TTL (fresh full window, re-sized
    from the live user count — a user added mid-cycle is covered).

    The worker calls this once per matched user. With the heartbeat, a
    LIVE hunt never expires its own TTL; without a holder, TTL expiry
    remains the self-heal for a crashed process. Returns False when the
    claim is no longer ours (stolen after a heartbeat gap): the caller
    logs it and finishes its cycle — the stealer is already running and
    dedupe/upsert keep the pool consistent.
    """
    import datetime

    from sqlalchemy import update

    from app.core.timeutil import utc_now
    from app.models import SystemLock

    result = db.execute(
        update(SystemLock)
        .where(
            SystemLock.name == "hunt",
            SystemLock.owner_token == owner_token,
            SystemLock.locked_until.isnot(None),
        )
        .values(locked_until=utc_now() + datetime.timedelta(minutes=_claim_ttl_minutes(db)))
    )
    db.commit()
    return result.rowcount == 1


def run_scheduled_hunt() -> dict:
    """One hunt cycle under the claim lock: ONE delta scrape per country
    (the UNION of every onboarded user's queries and municipalities —
    the pool stops being shaped by whoever last pressed Hunt), then a
    matching pass per user. The claim is ALWAYS released by its owner."""
    from app.models import Profile
    from app.services.pipeline import (
        _maintenance_sweeps,
        build_union_contexts,
        match_for_user,
        scrape_for_context,
    )

    claim_token = None
    db = SessionLocal()
    try:
        claim_token = claim_hunt(db)
        if not claim_token:
            logger.info("Hunt lock held elsewhere — skipping this cycle")
            return {"status": "skipped", "reason": "lock_held"}
    finally:
        db.close()

    summary = {"status": "ran", "users": 0, "errors": 0}
    try:
        db = SessionLocal()
        try:
            union_ctxs = build_union_contexts(db)
            for ctx in union_ctxs:
                for s in scrape_for_context(db, ctx):
                    if s["status"] == "failed":
                        summary["errors"] += 1
            _maintenance_sweeps(db)
            user_ids = [
                row[0]
                for row in db.query(Profile.user_id)
                .filter(Profile.country.isnot(None),
                        Profile.user_id.isnot(None))
                .distinct().all()
            ]
        finally:
            db.close()
        if not user_ids:
            logger.info("Scheduled hunt: no onboarded users")
            return {"status": "ran", "users": 0, "errors": summary["errors"]}

        for uid in user_ids:
            # WO-04 review: scheduled hunts carry their user's id onto
            # ai_usage rows — most spend flows through here, and WO-14's
            # trial budget meters it per user
            token = current_user_id.set(uid)
            try:
                db = SessionLocal()
                try:
                    # PIPE-18 heartbeat: keep OUR claim alive across the
                    # per-user matching passes (each may spend a full
                    # MATCH_TIME_BUDGET_SECONDS), re-sized for users
                    # onboarded since the claim.
                    if not renew_hunt(db, claim_token):
                        logger.warning(
                            "Hunt claim no longer ours (TTL stolen?) — "
                            "finishing this cycle; the stealer is running"
                        )
                    result = match_for_user(db, uid)
                finally:
                    db.close()
                if result.get("status") == "failed":
                    summary["errors"] += 1
                    logger.error("Scheduled match failed for user %s: %s",
                                 uid, result.get("error"))
                elif result.get("status") == "aborted":
                    # PIPE-19: the user vanished mid-run (GDPR erase).
                    # Not an error and not a served user — just stop.
                    logger.info(
                        "Scheduled match aborted for user %s (deleted mid-run)",
                        uid,
                    )
                else:
                    summary["users"] += 1
            except Exception as e:  # noqa: BLE001 — one user's failure never kills the cycle
                summary["errors"] += 1
                logger.error("Scheduled hunt failed for user %s: %s", uid, e)
            finally:
                current_user_id.reset(token)
    finally:
        # ALWAYS released BY ITS OWNER (review: a transient error between
        # claim and release leaked the claim = a TTL-length silent
        # outage; PIPE-18: only when the claim is still ours — an
        # overrunner must not free a stealer's claim)
        if claim_token:
            db = SessionLocal()
            try:
                if not release_hunt(db, claim_token):
                    logger.warning(
                        "Hunt release skipped: claim no longer ours (TTL stolen)"
                    )
            finally:
                db.close()
    logger.info("Scheduled hunt: %s", summary)
    return summary


def main(argv=None) -> int:
    """Worker entrypoint.

    Default: init DB, run the scheduler loop forever (the 24/7 worker
    deploy shape). `--once`: run ONE claim-hunt-release cycle and exit —
    the Render cron-job shape (WO-07): cron runs are billed per second
    of active runtime, so the process must exit when the hunt is done;
    the DB claim lock keeps overlapping schedules safe anyway.
    """

    import argparse

    logging.basicConfig(level=logging.INFO)

    ap = argparse.ArgumentParser(
        prog="worker",
        description="JobFinderOS hunt worker (scheduler loop or one-shot)",
    )
    ap.add_argument(
        "--once", action="store_true",
        help="run a single hunt cycle (claim -> hunt -> release) and exit",
    )
    args = ap.parse_args(argv)

    # Production posture (r5): refusing to hunt without Postgres lives in
    # Settings._production_guards — keyed on DEBUG=false (the single safety
    # switch) and run at import in BOTH processes. The earlier
    # ENVIRONMENT-keyed version here was a second, independent switch that
    # could be dropped without any signal; ENVIRONMENT is a label, not a guard.

    init_db()

    if args.once:
        summary = run_scheduled_hunt()
        logger.info("One-shot hunt complete: %s", summary)
        return 0

    from apscheduler.schedulers.blocking import BlockingScheduler

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_scheduled_hunt,
        "interval",
        minutes=settings.SCRAPE_INTERVAL_MINUTES,
        id="jobfinder_hunt",
        max_instances=1,
        coalesce=True,
    )
    logger.info("Worker started — hunt every %d minutes (claim-locked)",
                settings.SCRAPE_INTERVAL_MINUTES)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Worker stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
