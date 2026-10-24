from datetime import datetime, timezone
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler

from app.calendar.service import CalendarService
from app.calendar.catalog import TimetableCatalog
from app.db.repository import Repository

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
) -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        calendar_service.poll_sources_from_scheduler,
        "interval",
        seconds=30 * 60,
        id="refresh-umb-schedules",
        next_run_time=datetime.now(timezone.utc),
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
        hour=0,
        minute=0,
        timezone=ZoneInfo("Europe/Warsaw"),
        id="refresh-timetable-catalog",
        next_run_time=datetime.now(timezone.utc),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    return scheduler
