import asyncio
import logging
from math import ceil
import time
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_csrf
from app.calendar import gcal
from app.calendar.source import canonicalize_xlsx_url
from app.calendar.service import CalendarService
from app.db.repository import Repository
from app.jobs.queue import QueueFullError

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class IntegrationRequest(BaseModel):
    source_url: str = Field(min_length=8, max_length=2048)
    group_number: int = Field(ge=0, le=999)
    calendar_name: str | None = Field(default=None, min_length=1, max_length=100)


class GroupUpdate(BaseModel):
    group_number: int | None = Field(default=None, ge=0, le=999)
    calendar_name: str | None = Field(default=None, min_length=1, max_length=100)


def _services(request: Request) -> tuple[Repository, CalendarService]:
    return request.app.state.repository, request.app.state.calendar_service


def _public(integration: dict) -> dict:
    return {
        "id": integration["id"],
        "source_url": integration["source_url"],
        "group_number": int(integration["group_number"]),
        "calendar_name": integration["calendar_name"],
        "calendar_id": integration["calendar_id"],
        "created_at": integration["created_at"],
        "updated_at": integration["updated_at"],
        "last_sync_at": integration.get("last_sync_at"),
    }


def _record_sync_failure(repository: Repository, calendar_service: CalendarService, integration: dict, trigger: str) -> None:
    try:
        notice = repository.create_sync_failure_notification(integration, trigger)
        calendar_service.publish_notifications([notice])
    except Exception:
        LOG.exception("Could not save or send sync failure notification integration=%s", integration.get("id"))


async def _delete_unclaimed_calendar(
    user_id: str, calendar_id: str, request: Request, repository: Repository
) -> None:
    try:
        await asyncio.to_thread(
            gcal.delete_calendar, user_id, calendar_id, repository, request.app.state.crypto,
            request.app.state.metrics,
        )
    except Exception:
        LOG.exception("Could not remove unclaimed Google Calendar %s", calendar_id)


@router.get("")
async def list_integrations(request: Request, user: dict = Depends(current_user)) -> list[dict]:
    repository, _ = _services(request)
    return [_public(row) for row in repository.list_integrations(user["id"])]


def _validate_group_choice(parsed: dict, group_number: int) -> None:
    mode = parsed.get("grouping_mode", "unknown")
    available = parsed.get("available_groups", [])
    if mode == "all" and group_number != 0:
        raise HTTPException(status_code=422, detail="This plan has no group split; choose all activities.")
    if mode == "groups" and group_number not in available:
        choices = ", ".join(map(str, available))
        raise HTTPException(
            status_code=422,
            detail=f"Group {group_number} is not present in this workbook. Available groups: {choices}.",
        )
    if mode == "unknown" and group_number == 0:
        raise HTTPException(status_code=422, detail="This workbook does not identify groups. Enter your group number.")


@router.get("/workbook-info")
async def workbook_info(
    request: Request,
    source_url: str = Query(min_length=8, max_length=2048),
    user: dict = Depends(current_user),
) -> dict:
    repository, calendar_service = _services(request)
    try:
        canonical_url = canonicalize_xlsx_url(source_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    accepted, retry_at = repository.reserve_workbook_inspection(user["id"], canonical_url)
    if not accepted:
        retry_seconds = max(1, ceil((retry_at or 0) - time.time()))
        raise HTTPException(
            status_code=429,
            detail="You have reached the workbook inspection limit. Try again later.",
            headers={"Retry-After": str(retry_seconds)},
        )
    try:
        parsed = await calendar_service.get_source(canonical_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("Could not inspect UMB schedule workbook")
        raise HTTPException(status_code=502, detail="Could not read this UMB schedule workbook") from exc
    return {
        "source_url": canonical_url,
        "format_id": parsed.get("format_id"),
        "grouping_mode": parsed.get("grouping_mode", "unknown"),
        "available_groups": parsed.get("available_groups", []),
    }


@router.post("", status_code=201, dependencies=[Depends(require_csrf)])
async def create_integration(payload: IntegrationRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    if payload.calendar_name is not None and not payload.calendar_name.strip():
        raise HTTPException(status_code=422, detail="Calendar name cannot be empty")
    if repository.list_integrations(user["id"]):
        raise HTTPException(status_code=409, detail="This Google account already has an integration. Delete it before setting up another.")
    try:
        source_url = canonicalize_xlsx_url(payload.source_url)
        parsed = await calendar_service.get_source(source_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        LOG.exception("Could not load UMB schedule during integration setup")
        raise HTTPException(status_code=502, detail="Could not read this UMB schedule workbook") from exc

    _validate_group_choice(parsed, payload.group_number)
    calendar_name = (payload.calendar_name or repository.default_calendar_name(payload.group_number)).strip()
    try:
        calendar_id = await asyncio.to_thread(
            gcal.create_calendar, user["id"], repository, request.app.state.crypto, calendar_name,
            request.app.state.metrics,
        )
    except Exception as exc:
        LOG.exception("Could not create the app-managed Google Calendar")
        raise HTTPException(status_code=502, detail="Could not create the dedicated Google Calendar") from exc

    try:
        integration = repository.create_integration(
            user_id=user["id"], source_url=source_url, group_number=payload.group_number,
            calendar_id=calendar_id, calendar_name=calendar_name,
        )
    except sqlite3.IntegrityError as exc:
        await _delete_unclaimed_calendar(user["id"], calendar_id, request, repository)
        raise HTTPException(status_code=409, detail="This Google account already has an integration") from exc
    except Exception as exc:
        await _delete_unclaimed_calendar(user["id"], calendar_id, request, repository)
        LOG.exception("Could not save the integration after creating its Google Calendar")
        raise HTTPException(status_code=500, detail="Could not save this integration") from exc

    try:
        cache = repository.get_source_cache(source_url)
        await request.app.state.job_queue.enqueue(
            "reconcile",
            {"integration_id": integration["id"], "user_id": user["id"], "source_url": source_url,
             "expected_sha256": cache["content_sha256"] if cache else None,
             "emit_notifications": False},
            trigger="integration_created",
            dedupe_key=f"initial-reconcile:{integration['id']}",
        )
    except QueueFullError as exc:
        LOG.warning("Could not queue initial reconciliation because queue is full integration=%s", integration["id"])
        repository.delete_integration(integration["id"], user["id"])
        await _delete_unclaimed_calendar(user["id"], calendar_id, request, repository)
        raise HTTPException(status_code=503, detail="The service is busy; try setting up the integration again") from exc
    return {**_public(repository.get_integration(integration["id"], user["id"])),
            "initial_sync_status": "queued"}


@router.patch("/{integration_id}", dependencies=[Depends(require_csrf)])
async def update_integration(integration_id: str, payload: GroupUpdate, request: Request,
                             response: Response, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    existing = repository.get_integration(integration_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    if payload.group_number is None and payload.calendar_name is None:
        raise HTTPException(status_code=422, detail="Provide a group number or calendar name")
    if payload.calendar_name is not None and not payload.calendar_name.strip():
        raise HTTPException(status_code=422, detail="Calendar name cannot be empty")
    if payload.group_number is None:
        calendar_name = payload.calendar_name.strip()
        try:
            await asyncio.to_thread(calendar_service.rename_calendar, existing, calendar_name)
        except Exception as exc:
            LOG.exception("Could not rename the app-managed Google Calendar")
            _record_sync_failure(repository, calendar_service, existing, "integration_changed")
            raise HTTPException(status_code=502, detail="Could not rename the dedicated Google Calendar") from exc
        integration = repository.update_group(integration_id, user["id"], calendar_name=calendar_name)
        notice = repository.create_sync_completion_notification(integration, "integration_changed")
        calendar_service.publish_notifications([notice])
        return _public(integration)
    try:
        parsed = await calendar_service.get_source(
            existing["source_url"], suppress_integration_id=integration_id
        )
    except Exception as exc:
        LOG.exception("Could not load schedule while updating integration")
        _record_sync_failure(repository, calendar_service, existing, "integration_changed")
        raise HTTPException(status_code=502, detail="Could not read this UMB schedule workbook") from exc
    if payload.group_number is not None:
        _validate_group_choice(parsed, payload.group_number)
    integration = repository.update_group(
        integration_id,
        user["id"],
        payload.group_number,
        payload.calendar_name,
    )
    try:
        cache = repository.get_source_cache(integration["source_url"])
        await request.app.state.job_queue.enqueue(
            "reconcile",
            {"integration_id": integration_id, "user_id": user["id"],
             "source_url": integration["source_url"],
             "expected_sha256": cache["content_sha256"] if cache else None,
             "emit_notifications": False},
            trigger="integration_changed",
            dedupe_key=f"integration-change:{integration_id}:{integration['updated_at']}",
        )
        response.status_code = 202
    except QueueFullError as exc:
        raise HTTPException(status_code=503, detail="The group was saved, but the service is busy; sync it again shortly") from exc
    return {**_public(repository.get_integration(integration_id, user["id"])),
            "sync_status": "queued"}


@router.post("/{integration_id}/sync", status_code=202, dependencies=[Depends(require_csrf)])
async def sync_now(integration_id: str, request: Request, response: Response,
                   user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    integration = repository.get_integration(integration_id, user["id"])
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    accepted, eligible_at = repository.reserve_manual_sync(integration_id, user["id"])
    if not accepted:
        if eligible_at is None:
            raise HTTPException(status_code=404, detail="Integration not found")
        response.status_code = 200
        return {"status": "skipped", "retry_at": eligible_at}
    reserved = repository.get_integration(integration_id, user["id"])
    try:
        await request.app.state.job_queue.enqueue(
            "manual_sync", {"integration_id": integration_id, "user_id": user["id"]},
            trigger="manual", dedupe_key=None,
        )
    except QueueFullError as exc:
        repository.release_manual_sync(integration_id, user["id"], reserved.get("last_sync_at"))
        raise HTTPException(status_code=503, detail="The service is busy; retry shortly", headers={"Retry-After": "60"}) from exc
    return {"status": "queued"}


@router.delete("/{integration_id}", dependencies=[Depends(require_csrf)])
async def delete_integration(integration_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    integration = repository.get_integration(integration_id, user["id"])
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    await request.app.state.job_queue.cancel(
        lambda _kind, payload: payload.get("integration_id") == integration_id
    )
    try:
        calendar_deleted = await asyncio.to_thread(calendar_service.delete_integration, integration)
    except Exception as exc:
        LOG.exception("Could not delete app-managed Google Calendar")
        raise HTTPException(status_code=502, detail="Could not delete the dedicated Google Calendar") from exc
    notice = repository.create_integration_deletion_notification(integration, calendar_deleted)
    calendar_service.publish_notifications([notice])
    return {"deleted": True, "calendar_deleted": calendar_deleted}
