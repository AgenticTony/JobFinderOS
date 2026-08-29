"""
Optional background scheduler — runs the scrape + match pipeline periodically.

Enabled with ENABLE_SCHEDULER=true and SCRAPE_INTERVAL_MINUTES=<n>.
Disabled by default; the pipeline can always be triggered via the API.
"""

import logging
from datetime import datetime, timedelta, timezone

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
        # WO-04: dev-mode scheduled hunts go through the SAME claim-locked
        # cycle as the worker — otherwise dev single-process mode bypasses
        # the exclusion the worker enforces (review finding).
        from app.services.worker import run_scheduled_hunt

        run_scheduled_hunt()

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


def next_run_from_fixed_times(times_csv: str) -> datetime | None:
    """Next occurrence of fixed daily UTC times ("HH:MM,HH:MM").

    Describes the EXTERNAL cron (render.yaml) when the in-process
    scheduler is off — the dashboard countdown needs a next-run
    timestamp the APScheduler cannot provide there.
    """
    times: list[tuple[int, int]] = []
    for part in times_csv.split(","):
        try:
            h, m = part.strip().split(":")
            h, m = int(h), int(m)
        except ValueError:
            continue
        if 0 <= h <= 23 and 0 <= m <= 59:
            times.append((h, m))
    if not times:
        return None
    now = datetime.now(timezone.utc)
    today = sorted(
        now.replace(hour=h, minute=m, second=0, microsecond=0)
        for h, m in times
    )
    future = [t for t in today if t > now]
    return future[0] if future else today[0] + timedelta(days=1)


def stop_scheduler() -> None:
    """Stop the scheduler on shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")
