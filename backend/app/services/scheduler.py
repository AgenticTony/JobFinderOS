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
        from app.services.pipeline import run_pipeline

        try:
            summary = run_pipeline()
            logger.info("Scheduled pipeline: %s", summary)
        except Exception as e:
            logger.error("Scheduled pipeline failed: %s", e)

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


def stop_scheduler() -> None:
    """Stop the scheduler on shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
