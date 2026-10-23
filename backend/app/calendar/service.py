from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import logging
from threading import Lock

from google.auth.exceptions import RefreshError

from app.calendar import gcal
from app.calendar.reconcile import reconcile_integration
from app.calendar.source import fetch_and_parse
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
    def __init__(self, repository: Repository, crypto: EnvelopeCrypto):
        self.repository = repository
        self.crypto = crypto
        self._locks_guard = Lock()
        self._integration_locks: dict[str, Lock] = {}

    @contextmanager
    def integration_lock(self, integration_id: str):
        with self._locks_guard:
            lock = self._integration_locks.setdefault(integration_id, Lock())
        with lock:
            yield

    def reconcile(self, integration: dict, parsed: dict) -> dict:
        with self.integration_lock(integration["id"]):
            return reconcile_integration(integration, parsed, self.repository, self.crypto)

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

    async def get_source(self, source_url: str) -> dict:
        cache = self.repository.get_source_cache(source_url)
        if _cache_fresh(cache):
            return cache["parsed"]
        await self.refresh_source(source_url)
        cache = self.repository.get_source_cache(source_url)
        if not _cache_fresh(cache):
            raise RuntimeError("The UMB schedule is unavailable or its cached copy has expired")
        return cache["parsed"]

    async def refresh_source(self, source_url: str, required_integration_id: str | None = None) -> tuple[bool, dict]:
        digest, parsed = await fetch_and_parse(source_url)
        changed = self.repository.upsert_source_cache(source_url, digest, parsed)
        for integration in self.repository.integrations_for_source(source_url):
            if not changed and integration.get("last_reconciled_sha256") == digest:
                continue
            try:
                await asyncio.to_thread(
                    self.reconcile, integration, parsed
                )
                self.repository.mark_reconciled(integration["id"], digest)
            except Exception:
                LOG.exception("Could not reconcile integration %s for updated source", integration["id"])
                if integration["id"] == required_integration_id:
                    raise
        return changed, parsed

    async def poll_sources(self) -> None:
        for source_url in self.repository.all_source_urls():
            try:
                changed, parsed = await self.refresh_source(source_url)
                LOG.info("Source poll complete url=%s changed=%s entries=%d", source_url, changed, len(parsed.get("events", [])))
            except Exception:
                LOG.exception("Could not refresh UMB schedule source %s", source_url)
        removed = self.repository.delete_expired_source_cache(12)
        if removed:
            LOG.info("Removed %d expired UMB source cache entries", removed)

    async def sync_integration(self, integration: dict, force_remote_read: bool = False) -> dict:
        if force_remote_read:
            changed, parsed = await self.refresh_source(integration["source_url"], integration["id"])
            if changed:
                return {"source_changed": True, "reconciled": "all"}
        else:
            parsed = await self.get_source(integration["source_url"])
        integration = self.repository.get_integration(integration["id"], integration["user_id"])
        if not integration:
            raise RuntimeError("Integration was deleted before synchronization completed")
        result = await asyncio.to_thread(self.reconcile, integration, parsed)
        cache = self.repository.get_source_cache(integration["source_url"])
        if cache:
            self.repository.mark_reconciled(integration["id"], cache["content_sha256"])
        return {"source_changed": False, **result}

    def poll_sources_from_scheduler(self) -> None:
        asyncio.run(self.poll_sources())
