"""
Scheduler — APScheduler running inside the existing asyncio event loop.
No extra process or thread needed.
"""
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import settings
from app.db import repository

logger = logging.getLogger(__name__)


def create_scheduler() -> AsyncIOScheduler:
    """Build and configure the scheduler. Caller is responsible for calling .start()."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        _cleanup_job,
        trigger="interval",
        hours=settings.CLEANUP_INTERVAL_HOURS,
        id="cleanup_old_posts",
        replace_existing=True,
        misfire_grace_time=600,  # allow up to 10-minute late fire
    )
    logger.info(
        f"Scheduler: cleanup job scheduled every {settings.CLEANUP_INTERVAL_HOURS}h "
        f"(retention={settings.MAX_POST_AGE_DAYS} days)"
    )
    return scheduler


async def _cleanup_job() -> None:
    logger.info("Scheduler: cleanup job triggered")
    try:
        count = await repository.delete_older_than(settings.MAX_POST_AGE_DAYS)
        logger.info(f"Scheduler: cleanup complete — {count} rows deleted")
    except Exception as exc:
        logger.error(f"Scheduler: cleanup job failed: {exc!r}")
