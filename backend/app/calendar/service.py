from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock

from google.auth.exceptions import RefreshError

from app.calendar import gcal
from app.calendar.reconcile import reconcile_integration
from app.calendar.parsers.registry import PARSER_CACHE_VERSION
from app.calendar.source import download_and_hash, parse_downloaded_source
from app.crypto.envelope import EnvelopeCrypto
from app.db.repository import Repository

LOG = logging.getLogger(__name__)
CACHE_MAX_AGE = timedelta(hours=12)


def _cache_fresh(cache: dict | None) -> bool:
    if not cache:
        return False
    fetched_at = datetime.fromisoformat(cache["fetched_at"])
    return datetime.now(timezone.utc) - fetched_at.astimezone(timezone.utc) < CACHE_MAX_AGE


class CalendarService:
    def __init__(self, repository: Repository, crypto: EnvelopeCrypto, push_service=None):
        self.repository = repository
        self.crypto = crypto
        self.push_service = push_service
        self._locks_guard = Lock()
        self._integration_locks: dict[str, Lock] = {}

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

    def rename_calendar(self, integration: dict, calendar_name: str) -> None:
        with self.integration_lock(integration["id"]):
            gcal.rename_calendar(
                integration["user_id"], integration["calendar_id"], calendar_name,
                self.repository, self.crypto,
            )

    def reconcile(
        self, integration: dict, parsed: dict, *, emit_notifications: bool = True,
        sync_trigger: str = "scheduled",
    ) -> dict:
        with self.integration_lock(integration["id"]):
            result = reconcile_integration(
                integration, parsed, self.repository, self.crypto,
                emit_notifications=emit_notifications, sync_trigger=sync_trigger,
            )
            notifications = result.pop("notifications", [])
            self.publish_notifications(notifications)
            return result

    def delete_integration(self, integration: dict) -> bool:
        with self.integration_lock(integration["id"]):
            calendar_deleted = True
            try:
                gcal.delete_calendar(
                    integration["user_id"], integration["calendar_id"], self.repository, self.crypto
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
        if _cache_fresh(cache):
            return cache["parsed"]
        await self.refresh_source(
            source_url,
            suppressed_integration_ids={suppress_integration_id} if suppress_integration_id else None,
        )
        cache = self.repository.get_source_cache(source_url)
        if not _cache_fresh(cache):
            raise RuntimeError("The UMB schedule is unavailable or its cached copy has expired")
        return cache["parsed"]

    async def refresh_source(
        self, source_url: str, required_integration_id: str | None = None,
        sync_trigger: str = "scheduled",
        suppressed_integration_ids: set[str] | None = None,
    ) -> tuple[bool, bool, dict]:
        digest, contents = await download_and_hash(source_url)
        cache = self.repository.get_source_cache(source_url)
        integrations = self.repository.integrations_for_source(source_url)
        same_source = bool(cache and cache["content_sha256"] == digest)
        same_parser = bool(cache and cache["parsed"].get("_parser_version") == PARSER_CACHE_VERSION)

        if same_source and same_parser:
            # The workbook is unchanged. Reuse the parsed cache and do not
            # schedule reconciliation work during the source poll.
            if self.repository.touch_source_cache(source_url):
                return False, False, cache["parsed"]
            # If another operation removed the row after it was read, restore
            # the verified parsed representation without parsing the workbook.
            self.repository.upsert_source_cache(source_url, digest, cache["parsed"])
            return False, False, cache["parsed"]

        parsed = await parse_downloaded_source(contents, source_url)
        parsed["_parser_version"] = PARSER_CACHE_VERSION
        parser_changed = bool(cache and not same_parser)
        source_changed = bool(cache and not same_source)
        if cache is None:
            source_changed = any(
                integration.get("last_reconciled_sha256") != digest
                for integration in integrations
            ) or not integrations
        self.repository.upsert_source_cache(source_url, digest, parsed)

        if not source_changed and not parser_changed:
            return False, False, parsed

        reconciled_all = bool(integrations)
        for integration in integrations:
            try:
                await asyncio.to_thread(
                    self.reconcile, integration, parsed,
                    emit_notifications=integration["id"] not in (suppressed_integration_ids or set()),
                    sync_trigger=("manual" if integration["id"] == required_integration_id and sync_trigger == "manual"
                                  else "integration_changed" if integration["id"] in (suppressed_integration_ids or set())
                                  else "source_changed" if source_changed else sync_trigger),
                )
                self.repository.mark_reconciled(integration["id"], digest)
            except Exception:
                LOG.exception("Could not reconcile integration %s for updated source", integration["id"])
                if integration["id"] == required_integration_id:
                    raise
        return source_changed, reconciled_all, parsed

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
            LOG.info("Removed %d expired UMB source cache entries", removed)

    async def sync_integration(self, integration: dict, force_remote_read: bool = False) -> dict:
        if force_remote_read:
            changed, reconciled, _parsed = await self.refresh_source(
                integration["source_url"], integration["id"], "manual"
            )
            if reconciled:
                return {"source_changed": changed, "reconciled": "all"}
            return {
                "source_changed": changed,
                "reconciled": "none",
                "added_or_updated": 0,
                "removed": 0,
            }
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
