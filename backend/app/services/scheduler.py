"""
Optional background scheduler — runs the scrape + match pipeline periodically.

Enabled with ENABLE_SCHEDULER=true and SCRAPE_INTERVAL_MINUTES=<n>.
Disabled by default; the pipeline can always be triggered via the API.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.core.config import settings

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Start the background pipeline scheduler if enabled."""
    global _scheduler
    if not settings.ENABLE_SCHEDULER:
        logger.info("Scheduler disabled (ENABLE_SCHEDULER=false)")
        return

    _scheduler = BackgroundScheduler(daemon=True)

    def _run_pipeline():
        from app.core.database import SessionLocal
        from app.models import Profile
        from app.services.pipeline import run_pipeline

        # One hunt per onboarded user (staggered by the job interval);
        # each user's context drives their own pack/gates/matching.
        db = SessionLocal()
        try:
            user_ids = [
                row[0]
                for row in db.query(Profile.user_id)
                .filter(Profile.country.isnot(None), Profile.user_id.isnot(None))
                .distinct()
                .all()
            ]
        finally:
            db.close()
        if not user_ids:
            logger.info("Scheduled pipeline: no onboarded users — nothing to do")
            return
        for uid in user_ids:
            try:
                summary = run_pipeline(user_id=uid)
                logger.info("Scheduled pipeline (user %s): %s", uid, summary)
            except Exception as e:
                logger.error("Scheduled pipeline failed for user %s: %s", uid, e)

    _scheduler.add_job(
        _run_pipeline,
        "interval",
        minutes=settings.SCRAPE_INTERVAL_MINUTES,
        id="jobfinder_pipeline",
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info(
        "Scheduler started — pipeline every %d minutes", settings.SCRAPE_INTERVAL_MINUTES
    )


def get_next_run_time():
    """Next scheduled pipeline run (datetime) or None if scheduler is off."""
    if _scheduler is None:
        return None
    job = _scheduler.get_job("jobfinder_pipeline")
    return job.next_run_time if job else None


def stop_scheduler() -> None:
    """Stop the scheduler on shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
