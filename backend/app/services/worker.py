"""The worker process — scheduler + hunts, OUT of the API (WO-04/D3).

Two API replicas used to mean two in-process schedulers racing the
same hunt. Now: the API lifespan never starts a scheduler in
production (ENABLE_SCHEDULER defaults false), and THIS entrypoint is
the only thing that hunts:

    python -m app.worker

Every scheduled cycle claims the DB hunt lock first — portable
(SQLite + Postgres), TTL-stealable (a crashed holder self-heals),
always released. A double-started worker skips harmlessly.
"""

import logging

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.services.ai_service import current_user_id

logger = logging.getLogger(__name__)

CLAIM_TTL_MINUTES = 45  # a hunt cycle's worst-case budget (matching
                         # time-budget default 300s + scrape + retries)


def claim_hunt(db) -> bool:
    """Claim the hunt lock. True = this process runs the cycle; False =
    someone else holds it (skip, don't error). Stale claims (crashed
    holder past TTL) are stealable.

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
    new_until = now + datetime.timedelta(minutes=CLAIM_TTL_MINUTES)

    result = db.execute(
        update(SystemLock)
        .where(
            SystemLock.name == "hunt",
            or_(SystemLock.locked_until.is_(None),
                SystemLock.locked_until <= now),
        )
        .values(locked_until=new_until)
    )
    db.commit()
    if result.rowcount == 1:
        return True

    # rowcount 0: either held (False) or the row does not exist yet —
    # first-ever claim via INSERT; the PK makes a second inserter lose
    db.rollback()
    db.add(SystemLock(name="hunt", locked_until=new_until))
    try:
        db.commit()
        return True
    except Exception:  # noqa: BLE001 — PK collision = another process claimed first
        db.rollback()
        return False


def release_hunt(db) -> None:
    """Release the claim. Idempotent — safe on the crashed-after-release
    path."""
    from app.models import SystemLock

    row = db.query(SystemLock).filter(SystemLock.name == "hunt").first()
    if row is not None:
        row.locked_until = None
        db.add(row)
        db.commit()


def run_scheduled_hunt() -> dict:
    """One hunt cycle under the claim lock: every onboarded user gets
    their per-user pipeline run. The claim is ALWAYS released."""
    from app.models import Profile
    from app.services.pipeline import run_pipeline

    db = SessionLocal()
    try:
        if not claim_hunt(db):
            logger.info("Hunt lock held elsewhere — skipping this cycle")
            return {"status": "skipped", "reason": "lock_held"}
    finally:
        db.close()

    summary = {"status": "ran", "users": 0, "errors": 0}
    try:
        db = SessionLocal()
        try:
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
            return {"status": "ran", "users": 0, "errors": 0}

        for uid in user_ids:
            # WO-04 review: scheduled hunts carry their user's id onto
            # ai_usage rows — most spend flows through here, and WO-14's
            # trial budget meters it per user
            token = current_user_id.set(uid)
            try:
                run_pipeline(user_id=uid)
                summary["users"] += 1
            except Exception as e:  # noqa: BLE001 — one user's failure never kills the cycle
                summary["errors"] += 1
                logger.error("Scheduled hunt failed for user %s: %s", uid, e)
            finally:
                current_user_id.reset(token)
    finally:
        # ALWAYS released (review: a transient error between claim and
        # release leaked the claim = 45-minute silent outage)
        db = SessionLocal()
        try:
            release_hunt(db)
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

    # Production posture guard (WO-07 live incident): the recreated cron
    # ran 'successfully' in 13s against container-local SQLite — its
    # DATABASE_URL was empty (sync:false prompts only happen at INITIAL
    # blueprint creation, and the service was created by a later sync).
    # A silent no-op hunt is the worst failure mode; fail loudly instead.
    if (settings.ENVIRONMENT == "production"
            and not settings.DATABASE_URL.startswith("postgres")):
        logger.error(
            "REFUSING to hunt: ENVIRONMENT=production but DATABASE_URL is "
            "not Postgres (likely unset — check the service's environment. "
            "A hunt against throwaway SQLite silently does nothing.)")
        return 1

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
