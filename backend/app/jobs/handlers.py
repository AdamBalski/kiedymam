from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import email.utils
import logging
import time

import httpx

from app.calendar.service import CalendarService
from app.db.repository import Repository

LOG = logging.getLogger(__name__)


def _retry_delay(error: Exception, attempt: int) -> float | None:
    response = error.response if isinstance(error, httpx.HTTPStatusError) else None
    status = response.status_code if response is not None else getattr(getattr(error, "resp", None), "status", None)
    retry_after = (response.headers.get("Retry-After") if response is not None
                   else getattr(getattr(error, "resp", None), "get", lambda _key: None)("retry-after"))
    retryable = status in (429, 500, 502, 503, 504) or isinstance(error, httpx.TransportError)
    if not retryable or attempt >= 5:
        return None
    if retry_after:
        try:
            return min(3600, max(1, float(retry_after)))
        except ValueError:
            try:
                parsed = email.utils.parsedate_to_datetime(retry_after)
                return min(3600, max(1, (parsed - datetime.now(timezone.utc)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                pass
    return float(60 * 2 ** (attempt - 1))


async def _enqueue_retry(calendar_service: CalendarService, kind: str, payload: dict,
                         trigger: str, attempt: int, error: Exception) -> bool:
    delay = _retry_delay(error, attempt)
    if delay is None:
        return False
    retry_payload = {**payload, "retry_attempt": attempt + 1}
    await calendar_service.job_queue.enqueue(
        kind, retry_payload, trigger=trigger, delay_seconds=delay,
        dedupe_key=f"{kind}-retry:{payload.get('integration_id', payload.get('source_url'))}:{attempt + 1}",
    )
    if calendar_service.metrics:
        calendar_service.metrics.add_counter("background.job.retry.count", attributes={
            "queue.name": kind, "sync.trigger": trigger, "outcome": "scheduled",
        })
    return True


def build_job_handlers(repository: Repository, calendar_service: CalendarService) -> dict:
    async def source_refresh(payload: dict) -> dict:
        source_url = payload["source_url"]
        try:
            changed, reconciliations, parsed = await calendar_service.refresh_source(source_url)
            calendar_service.last_successful_source_poll = time.time()
        except Exception as exc:
            removed = repository.delete_expired_source_cache(12)
            if removed:
                LOG.warning("Removed expired parsed cache after source refresh failure count=%d", removed)
            attempt = int(payload.get("retry_attempt", 1))
            try:
                await _enqueue_retry(calendar_service, "source_refresh", {"source_url": source_url},
                                     "scheduled_poll", attempt, exc)
            except Exception:
                LOG.exception("Could not enqueue source refresh retry")
            raise
        return {"source_changed": changed, "reconciliations_queued": reconciliations,
                "event_count": len(parsed.get("events", []))}

    async def reconcile(payload: dict) -> dict:
        integration_id = payload["integration_id"]
        user_id = payload["user_id"]
        trigger = payload.get("_trigger", "source_changed")
        integration = repository.get_integration(integration_id, user_id)
        if not integration:
            return {"superseded": True, "reason": "integration_deleted"}
        try:
            parsed = await calendar_service.get_source(integration["source_url"])
            cache = repository.get_source_cache(integration["source_url"])
            expected = payload.get("expected_sha256")
            if expected and cache and expected != cache["content_sha256"]:
                await calendar_service._schedule_stale_integrations(
                    integration["source_url"], cache["content_sha256"],
                    repository.integrations_for_source(integration["source_url"]), None,
                )
                return {"superseded": True, "reason": "source_sha_changed"}
            result = await asyncio.to_thread(
                calendar_service.reconcile,
                integration,
                parsed,
                emit_notifications=bool(payload.get("emit_notifications", True)),
                sync_trigger=trigger,
            )
            if cache:
                repository.mark_reconciled(integration_id, cache["content_sha256"])
            notice = repository.create_sync_completion_notification(integration, trigger)
            calendar_service.publish_notifications([notice])
            return {key: result[key] for key in ("added_or_updated", "removed")}
        except Exception as exc:
            LOG.exception("Reconciliation job failed integration=%s trigger=%s", integration_id, trigger)
            attempt = int(payload.get("retry_attempt", 1))
            try:
                retrying = await _enqueue_retry(
                    calendar_service, "reconcile", payload, trigger, attempt, exc,
                )
            except Exception:
                retrying = False
                LOG.exception("Could not enqueue reconciliation retry integration=%s", integration_id)
            current = repository.get_integration(integration_id, user_id)
            if current and not retrying:
                notice = repository.create_sync_failure_notification(current, trigger)
                calendar_service.publish_notifications([notice])
            raise

    async def manual_sync(payload: dict) -> dict:
        integration = repository.get_integration(payload["integration_id"], payload["user_id"])
        if not integration:
            return {"superseded": True, "reason": "integration_deleted"}
        try:
            result = await calendar_service.sync_integration(integration, force_remote_read=True)
            current = repository.get_integration(integration["id"], integration["user_id"])
            if current:
                notice = repository.create_sync_completion_notification(current, "manual")
                calendar_service.publish_notifications([notice])
            return {key: result[key] for key in ("source_changed", "added_or_updated", "removed") if key in result}
        except Exception as exc:
            LOG.exception("Manual sync job failed integration=%s", integration["id"])
            attempt = int(payload.get("retry_attempt", 1))
            try:
                retrying = await _enqueue_retry(
                    calendar_service, "manual_sync", payload, "manual", attempt, exc,
                )
            except Exception:
                retrying = False
                LOG.exception("Could not enqueue manual sync retry integration=%s", integration["id"])
            current = repository.get_integration(integration["id"], integration["user_id"])
            if current and not retrying:
                notice = repository.create_sync_failure_notification(current, "manual")
                calendar_service.publish_notifications([notice])
            raise

    handlers = {
        "source_refresh": source_refresh,
        "reconcile": reconcile,
        "manual_sync": manual_sync,
    }
    return handlers
