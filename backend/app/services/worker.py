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

logger = logging.getLogger(__name__)

CLAIM_TTL_MINUTES = 45  # a hunt cycle's worst-case budget (matching
                         # time-budget default 300s + scrape + retries)


def claim_hunt(db) -> bool:
    """Claim the hunt lock. True = this process runs the cycle; False =
    someone else holds it (skip, don't error). Stale claims (crashed
    holder past TTL) are stealable."""
    import datetime

    from app.core.timeutil import utc_now
    from app.models import SystemLock

    now = utc_now()
    ttl = datetime.timedelta(minutes=CLAIM_TTL_MINUTES)
    row = db.query(SystemLock).filter(SystemLock.name == "hunt").first()
    if row is None:
        # first-ever claim: INSERT the row (get-or-create without a race
        # window — a second inserter fails the PK and simply loses)
        db.add(SystemLock(name="hunt", locked_until=now + ttl))
        try:
            db.commit()
            return True
        except Exception:  # noqa: BLE001 — PK collision = another process claimed first
            db.rollback()
            return False
    if row.locked_until is not None and row.locked_until > now:
        return False
    row.locked_until = now + ttl
    db.add(row)
    db.commit()
    return True


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
    db = SessionLocal()
    try:
        user_ids = [
            row[0]
            for row in db.query(Profile.user_id)
            .filter(Profile.country.isnot(None), Profile.user_id.isnot(None))
            .distinct().all()
        ]
    finally:
        db.close()
    if not user_ids:
        logger.info("Scheduled hunt: no onboarded users")
        db = SessionLocal()
        try:
            release_hunt(db)
        finally:
            db.close()
        return {"status": "ran", "users": 0, "errors": 0}

    for uid in user_ids:
        try:
            run_pipeline(user_id=uid)
            summary["users"] += 1
        except Exception as e:  # noqa: BLE001 — one user's failure never kills the cycle
            summary["errors"] += 1
            logger.error("Scheduled hunt failed for user %s: %s", uid, e)
    db = SessionLocal()
    try:
        release_hunt(db)
    finally:
        db.close()
    logger.info("Scheduled hunt: %s", summary)
    return summary


def main() -> int:
    """Worker entrypoint: init DB, run the scheduler loop forever."""

    from apscheduler.schedulers.blocking import BlockingScheduler

    logging.basicConfig(level=logging.INFO)
    init_db()
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
