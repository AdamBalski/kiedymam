from datetime import datetime, timezone
from hashlib import sha256
import logging
from zoneinfo import ZoneInfo
import asyncio

from apscheduler.schedulers.background import BackgroundScheduler

from app.calendar.service import CalendarService
from app.calendar.catalog import TimetableCatalog
from app.db.repository import Repository
from app.jobs.queue import InMemoryJobQueue

LOG = logging.getLogger(__name__)


def _daily_cleanup(repository: Repository) -> None:
    deleted = repository.clean_notifications()
    repository.clean_auth_records()
    if deleted:
        LOG.info("Removed %d notifications older than seven days", deleted)


def start_jobs(
    calendar_service: CalendarService,
    repository: Repository,
    timetable_catalog: TimetableCatalog,
    job_queue: InMemoryJobQueue,
    loop: asyncio.AbstractEventLoop,
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")

    def enqueue_due_sources() -> None:
        now = datetime.now(timezone.utc)
        half_hour = int(now.timestamp() // 1800)
        for source_url in repository.all_source_urls():
            slot = int.from_bytes(sha256(source_url.encode("utf-8")).digest()[:8], "big") % 30
            if now.minute not in (slot, slot + 30):
                continue
            job_queue.enqueue_from_scheduler(
                loop,
                "source_refresh",
                {"source_url": source_url},
                trigger="scheduled_poll",
                dedupe_key=f"source:{source_url}:{half_hour}",
            )

    scheduler.add_job(
        enqueue_due_sources,
        "cron",
        second=5,
        id="enqueue-staggered-umb-schedule-refreshes",
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        _daily_cleanup,
        "interval",
        days=1,
        id="daily-cleanup",
        args=[repository],
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        timetable_catalog.refresh,
        "cron",
        minute="*/30",
        timezone=ZoneInfo("Europe/Warsaw"),
        id="refresh-timetable-catalog",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
