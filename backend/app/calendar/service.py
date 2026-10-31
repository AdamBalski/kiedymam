from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock
from time import monotonic, time
from urllib.parse import urlsplit, urlunsplit

import httpx
from google.auth.exceptions import RefreshError

from app.calendar import gcal
from app.calendar.reconcile import reconcile_integration
from app.calendar.parsers.registry import parser_cache_version
from app.calendar.source import download_and_hash, download_latest_revision, parse_downloaded_source
from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository
from app.jobs.queue import QueueFullError
from app.limits.work import WorkLimits

LOG = logging.getLogger(__name__)
CACHE_MAX_AGE = timedelta(hours=12)
CACHE_MISSING_MAX_AGE = timedelta(minutes=30)


def _cache_fresh(cache: dict | None) -> bool:
    if not cache:
        return False
    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    max_age = CACHE_MISSING_MAX_AGE if cache.get("availability") == "missing" else CACHE_MAX_AGE
    return datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc) < max_age


def _raise_cached_missing(source_url: str, status: int) -> None:
    request = httpx.Request("GET", source_url)
    response = httpx.Response(status, request=request)
    raise httpx.HTTPStatusError(f"Cached UMB workbook response was HTTP {status}", request=request, response=response)


class CalendarService:
    def __init__(self, repository: Repository, crypto: EnvelopeCrypto, push_service=None, work_limits=None, metrics=None):
        self.repository = repository
        self.crypto = crypto
        self.push_service = push_service
        self.work_limits = work_limits or WorkLimits()
        self.metrics = metrics
        self.job_queue = None
        self.last_successful_source_poll: float | None = None
        self._locks_guard = Lock()
        self._integration_locks: dict[str, Lock] = {}
        self._source_locks_guard = Lock()
        self._source_locks: dict[str, asyncio.Lock] = {}

    @contextmanager
    def integration_lock(self, integration_id: str):
        with self._locks_guard:
            lock = self._integration_locks.setdefault(integration_id, Lock())
        with lock:
            yield

    def publish_notifications(self, notifications: list[dict]) -> None:
        if self.push_service:
            try:
                self.push_service.send_notifications(notifications)
            except Exception:
                LOG.exception("Could not dispatch push notifications")

    @staticmethod
    def _metric_url(source_url: str) -> str:
        parts = urlsplit(source_url)
        return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    def rename_calendar(self, integration: dict, calendar_name: str) -> None:
        with self.integration_lock(integration["id"]):
            gcal.rename_calendar(
                integration["user_id"], integration["calendar_id"], calendar_name,
                self.repository, self.crypto, self.metrics,
            )

    def reconcile(
        self, integration: dict, parsed: dict, *, emit_notifications: bool = True,
        sync_trigger: str = "scheduled",
    ) -> dict:
        started = monotonic()
        with self.integration_lock(integration["id"]):
            result = reconcile_integration(
                integration, parsed, self.repository, self.crypto,
                emit_notifications=emit_notifications, sync_trigger=sync_trigger,
                metrics=self.metrics,
            )
            notifications = result.pop("notifications", [])
            self.publish_notifications(notifications)
            if self.metrics:
                attributes = {
                    "operation.name": "calendar.reconcile",
                    "sync.trigger": sync_trigger,
                    "outcome": "success",
                    "xlsx.url": self._metric_url(integration["source_url"]),
                }
                self.metrics.observe("calendar.reconcile.duration", monotonic() - started, attributes)
                for action in ("added", "updated", "removed"):
                    count = int(result.get(action, 0))
                    self.metrics.add_counter(
                        "calendar.reconcile.event.count", count,
                        {**attributes, "outcome": action},
                    )
            return result

    def delete_integration(self, integration: dict) -> bool:
        with self.integration_lock(integration["id"]):
            calendar_deleted = True
            try:
                gcal.delete_calendar(
                    integration["user_id"], integration["calendar_id"], self.repository, self.crypto, self.metrics
                )
            except Exception as exc:
                status = gcal.http_error_status(exc)
                if status == 404:
                    pass
                elif status in (401, 403) or isinstance(exc, (RuntimeError, RefreshError)):
                    calendar_deleted = False
                    LOG.warning("Could not delete Google calendar after authorization was revoked: %s", integration["id"])
                else:
                    raise
            self.repository.delete_integration(integration["id"], integration["user_id"])
            return calendar_deleted

    async def get_source(self, source_url: str, suppress_integration_id: str | None = None) -> dict:
        cache = self.repository.get_source_cache(source_url)
        link_key = self.repository.timetable_plan_key_for_url(source_url)
        plan = self.repository.timetable_plan_by_key(link_key) if link_key else None
        # A catalog listing can return after a plan was marked missing, but only
        # a successful workbook fetch proves recovery. Bypass a good cached copy
        # once so the actual XLSX endpoint can confirm availability.
        verify_availability = bool(plan and plan.get("availability") == "missing")
        if _cache_fresh(cache):
            if cache.get("availability") == "missing":
                _raise_cached_missing(source_url, int(cache.get("http_status") or 404))
            if not verify_availability:
                if self.metrics:
                    self.metrics.add_counter("xlsx.cache.access", attributes={"outcome": "hit", "xlsx.url": self._metric_url(source_url)})
                    fetched = datetime.fromisoformat(cache["fetched_at"]).timestamp()
                    self.metrics.set_gauge("xlsx.source.cache.age", max(0, time() - fetched), {"xlsx.url": self._metric_url(source_url)})
                return cache["parsed"]
        if self.metrics:
            self.metrics.add_counter("xlsx.cache.access", attributes={"outcome": "miss", "xlsx.url": self._metric_url(source_url)})
        lock = await self._source_lock(source_url)
        async with lock:
            cache = self.repository.get_source_cache(source_url)
            link_key = self.repository.timetable_plan_key_for_url(source_url)
            plan = self.repository.timetable_plan_by_key(link_key) if link_key else None
            verify_availability = bool(plan and plan.get("availability") == "missing")
            if _cache_fresh(cache):
                if cache.get("availability") == "missing":
                    _raise_cached_missing(source_url, int(cache.get("http_status") or 404))
                if not verify_availability:
                    return cache["parsed"]
            await self._refresh_source_locked(
                source_url, None, "integration_changed" if suppress_integration_id else "scheduled",
                {suppress_integration_id} if suppress_integration_id else None,
            )
            cache = self.repository.get_source_cache(source_url)
            if not _cache_fresh(cache):
                raise RuntimeError("The UMB schedule is unavailable or its cached copy has expired")
            return cache["parsed"]

    async def _source_lock(self, source_url: str) -> asyncio.Lock:
        with self._source_locks_guard:
            lock = self._source_locks.get(source_url)
            if lock is None:
                lock = asyncio.Lock()
                self._source_locks[source_url] = lock
            return lock

    async def refresh_source(
        self, source_url: str, required_integration_id: str | None = None,
        sync_trigger: str = "scheduled",
        suppressed_integration_ids: set[str] | None = None,
    ) -> tuple[bool, bool, dict]:
        lock = await self._source_lock(source_url)
        async with lock:
            return await self._refresh_source_locked(
                source_url, required_integration_id, sync_trigger, suppressed_integration_ids
            )

    async def _refresh_source_locked(
        self, source_url: str, required_integration_id: str | None,
        sync_trigger: str, suppressed_integration_ids: set[str] | None,
    ) -> tuple[bool, bool, dict]:
        async with self.work_limits.umb_downloads:
            await self.work_limits.wait_for_umb_slot()
            download_started = monotonic()
            link_key = self.repository.timetable_plan_key_for_url(source_url)
            candidates = self.repository.timetable_revisions_by_key(link_key) if link_key else []
            effective_url = source_url
            try:
                if len(candidates) > 1:
                    effective_url, last_modified, digest, contents = await download_latest_revision(
                        candidates,
                        on_probe=lambda revisions: self.repository.update_timetable_revision_dates(
                            link_key, revisions
                        ),
                        downloader=download_and_hash,
                    )
                    self.repository.select_timetable_revision(link_key, effective_url, last_modified)
                else:
                    digest, contents = await download_and_hash(source_url)
            except Exception as error:
                attributes = {"outcome": "failure", "xlsx.url": self._metric_url(source_url)}
                status = getattr(getattr(error, "response", None), "status_code", None)
                if status in (404, 410):
                    self.repository.cache_missing_source(source_url, status)
                    link_key = self.repository.timetable_plan_key_for_url(source_url)
                    if self.metrics:
                        self.metrics.add_counter("umb.timetable.plan.unavailable.count", attributes={
                            "parser.key": self.repository.timetable_parser_key(source_url) or "unknown",
                            "outcome": "xlsx_not_found",
                        })
                    if link_key:
                        notices = self.repository.set_timetable_plan_availability(
                            link_key, False, expected_url=source_url
                        )
                        self.publish_notifications(notices)
                if isinstance(status, int):
                    attributes["http.response.status_code"] = status
                if self.metrics:
                    self.metrics.observe("xlsx.download.duration", monotonic() - download_started, attributes)
                raise
            download_duration = monotonic() - download_started
            if link_key:
                notices = self.repository.set_timetable_plan_availability(
                    link_key, True, expected_url=effective_url
                )
                self.publish_notifications(notices)
            if self.metrics:
                attributes = {"outcome": "success", "xlsx.url": self._metric_url(effective_url)}
                self.metrics.add_counter("xlsx.download.bytes", len(contents), {"xlsx.url": self._metric_url(effective_url)})
                self.metrics.observe("xlsx.download.duration", download_duration, attributes)
        source_url = effective_url
        cache = self.repository.get_source_cache(source_url)
        integrations = self.repository.integrations_for_source(source_url)
        parser_key = self.repository.timetable_parser_key(source_url)
        if not parser_key:
            raise RuntimeError("No parser key is assigned to this timetable link")
        same_source = bool(cache and cache["content_sha256"] == digest)
        current_parser_version = parser_cache_version()
        same_parser = bool(
            cache
            and cache["parsed"].get("_parser_version") == current_parser_version
            and cache["parsed"].get("_parser_key") == parser_key
        )

        if same_source and same_parser:
            if self.metrics:
                self.metrics.add_counter("xlsx.sha.result", attributes={"outcome": "unchanged", "xlsx.url": self._metric_url(source_url)})
            if self.repository.touch_source_cache(source_url):
                await self._schedule_stale_integrations(source_url, digest, integrations, required_integration_id)
                return False, False, cache["parsed"]
            self.repository.upsert_source_cache(source_url, digest, cache["parsed"])
            await self._schedule_stale_integrations(source_url, digest, integrations, required_integration_id)
            return False, False, cache["parsed"]

        parse_started = monotonic()

        def record_parser_result(parser_id: str | None) -> None:
            if not self.metrics:
                return
            attributes = {
                "outcome": "failure" if parser_id is None else "success",
                "xlsx.url": self._metric_url(source_url),
            }
            if parser_id is not None:
                attributes["parser.id"] = parser_id
            self.metrics.add_counter("xlsx.parser.attempt.count", 1, attributes)

        try:
            async with self.work_limits.xlsx_parses:
                parsed = await parse_downloaded_source(
                    contents, source_url, record_parser_result, parser_key=parser_key
                )
        except Exception:
            if self.metrics:
                self.metrics.observe("xlsx.parse.duration", monotonic() - parse_started,
                                     {"outcome": "failure", "xlsx.url": self._metric_url(source_url)})
            raise
        if self.metrics:
            parser_id = parsed.get("format_id", "unknown")
            attributes = {"parser.id": parser_id, "outcome": "success", "xlsx.url": self._metric_url(source_url)}
            self.metrics.add_counter("xlsx.event.count", len(parsed.get("events", [])), {"parser.id": parser_id, "xlsx.url": self._metric_url(source_url)})
            self.metrics.observe("xlsx.parse.duration", monotonic() - parse_started, attributes)
        parsed["_parser_version"] = current_parser_version
        parsed["_parser_key"] = parser_key
        parser_changed = bool(cache and not same_parser)
        source_changed = bool(cache and not same_source)
        if self.metrics:
            self.metrics.add_counter(
                "xlsx.sha.result", attributes={"outcome": "changed" if source_changed else "new", "xlsx.url": self._metric_url(source_url)}
            )
        if cache is None:
            source_changed = any(
                integration.get("last_reconciled_sha256") != digest
                for integration in integrations
            ) or not integrations
        self.repository.upsert_source_cache(source_url, digest, parsed)

        if source_changed or parser_changed or any(
            integration.get("last_reconciled_sha256") != digest for integration in integrations
        ):
            await self._schedule_stale_integrations(
                source_url, digest, integrations, required_integration_id,
                suppressed_integration_ids=suppressed_integration_ids,
                sync_trigger=sync_trigger,
            )
        return source_changed, bool(integrations), parsed

    async def _schedule_stale_integrations(
        self,
        source_url: str,
        digest: str,
        integrations: list[dict],
        required_integration_id: str | None,
        *,
        suppressed_integration_ids: set[str] | None = None,
        sync_trigger: str = "source_changed",
    ) -> None:
        stale = [item for item in integrations if item.get("last_reconciled_sha256") != digest
                 and item["id"] != required_integration_id]
        if not stale:
            return
        if not self.job_queue:
            parsed = self.repository.get_source_cache(source_url)["parsed"]
            for integration in stale:
                trigger = (
                    sync_trigger if integration["id"] in (suppressed_integration_ids or set())
                    else "source_changed"
                )
                await asyncio.to_thread(
                    self.reconcile, integration, parsed,
                    emit_notifications=integration["id"] not in (suppressed_integration_ids or set()),
                    sync_trigger=trigger,
                )
                self.repository.mark_reconciled(integration["id"], digest)
            return
        from hashlib import sha256
        stale.sort(key=lambda item: sha256(
            f"{source_url}{digest}{item['id']}".encode("utf-8")
        ).digest())
        total = len(stale)
        for index, integration in enumerate(stale):
            trigger = (
                sync_trigger if integration["id"] in (suppressed_integration_ids or set())
                else "source_changed"
            )
            try:
                await self.job_queue.enqueue(
                    "reconcile",
                    {"integration_id": integration["id"], "user_id": integration["user_id"], "source_url": source_url,
                     "expected_sha256": digest,
                     "emit_notifications": integration["id"] not in (suppressed_integration_ids or set())},
                    trigger=trigger,
                    delay_seconds=(index * 1800 / total),
                    dedupe_key=f"reconcile:{integration['id']}:{digest}",
                )
            except QueueFullError:
                LOG.warning("Reconciliation job remains pending for next source poll integration=%s", integration["id"])
                break

    async def poll_sources(self) -> None:
        for source_url in self.repository.all_source_urls():
            try:
                changed, reconciled, parsed = await self.refresh_source(source_url)
                LOG.info(
                    "Source poll complete url=%s changed=%s reconciled=%s entries=%d",
                    source_url,
                    changed,
                    reconciled,
                    len(parsed.get("events", [])),
                )
            except Exception:
                LOG.exception("Could not refresh UMB schedule source %s", source_url)
                removed = self.repository.delete_expired_source_cache(12)
                if removed:
                    LOG.info("Removed %d expired UMB source cache entries after failed refresh", removed)

    async def sync_integration(self, integration: dict, force_remote_read: bool = False) -> dict:
        if force_remote_read:
            changed, _reconciled, parsed = await self.refresh_source(
                integration["source_url"], integration["id"], "manual"
            )
            current = self.repository.get_integration(integration["id"], integration["user_id"])
            if not current:
                raise RuntimeError("Integration was deleted before synchronization completed")
            result = await asyncio.to_thread(self.reconcile, current, parsed, sync_trigger="manual")
            cache = self.repository.get_source_cache(current["source_url"])
            if cache:
                self.repository.mark_reconciled(current["id"], cache["content_sha256"])
            return {"source_changed": changed, **result}
        else:
            parsed = await self.get_source(integration["source_url"])
        integration = self.repository.get_integration(integration["id"], integration["user_id"])
        if not integration:
            raise RuntimeError("Integration was deleted before synchronization completed")
        result = await asyncio.to_thread(self.reconcile, integration, parsed, sync_trigger="manual")
        cache = self.repository.get_source_cache(integration["source_url"])
        if cache:
            self.repository.mark_reconciled(integration["id"], cache["content_sha256"])
        return {"source_changed": False, **result}

    def poll_sources_from_scheduler(self) -> None:
        asyncio.run(self.poll_sources())
