import asyncio
import logging
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_csrf
from app.calendar import gcal
from app.calendar.source import canonicalize_xlsx_url
from app.calendar.service import CalendarService
from app.db.repository import Repository

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api/integrations", tags=["integrations"])


class IntegrationRequest(BaseModel):
    source_url: str = Field(min_length=8, max_length=2048)
    group_number: int = Field(ge=1, le=36)


class GroupUpdate(BaseModel):
    group_number: int = Field(ge=1, le=36)


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


async def _delete_unclaimed_calendar(
    user_id: str, calendar_id: str, request: Request, repository: Repository
) -> None:
    try:
        await asyncio.to_thread(
            gcal.delete_calendar, user_id, calendar_id, repository, request.app.state.crypto
        )
    except Exception:
        LOG.exception("Could not remove unclaimed Google Calendar %s", calendar_id)


@router.get("")
async def list_integrations(request: Request, user: dict = Depends(current_user)) -> list[dict]:
    repository, _ = _services(request)
    return [_public(row) for row in repository.list_integrations(user["id"])]


@router.post("", dependencies=[Depends(require_csrf)])
async def create_integration(payload: IntegrationRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
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

    calendar_name = f"UMB Fizjoterapia — grupa 6 nr {payload.group_number}"
    try:
        calendar_id = await asyncio.to_thread(
            gcal.create_calendar, user["id"], repository, request.app.state.crypto, calendar_name
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
        await asyncio.to_thread(calendar_service.reconcile, integration, parsed)
        cache = repository.get_source_cache(source_url)
        if cache:
            repository.mark_reconciled(integration["id"], cache["content_sha256"])
    except Exception as exc:
        LOG.exception("Initial calendar reconciliation failed")
        repository.delete_integration(integration["id"], user["id"])
        await _delete_unclaimed_calendar(user["id"], calendar_id, request, repository)
        raise HTTPException(status_code=502, detail="The calendar was created, but the initial schedule sync failed") from exc
    return _public(repository.get_integration(integration["id"], user["id"]))


@router.patch("/{integration_id}", dependencies=[Depends(require_csrf)])
async def update_integration(integration_id: str, payload: GroupUpdate, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    existing = repository.get_integration(integration_id, user["id"])
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    integration = repository.update_group(integration_id, user["id"], payload.group_number)
    try:
        parsed = await calendar_service.get_source(integration["source_url"])
        integration = repository.get_integration(integration_id, user["id"])
        await asyncio.to_thread(calendar_service.reconcile, integration, parsed)
        cache = repository.get_source_cache(integration["source_url"])
        if cache:
            repository.mark_reconciled(integration_id, cache["content_sha256"])
    except Exception as exc:
        LOG.exception("Could not reconcile after group change")
        raise HTTPException(status_code=502, detail="Group was saved, but the calendar could not be reconciled") from exc
    return _public(repository.get_integration(integration_id, user["id"]))


@router.post("/{integration_id}/sync", dependencies=[Depends(require_csrf)])
async def sync_now(integration_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    integration = repository.get_integration(integration_id, user["id"])
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    accepted, eligible_at = repository.reserve_manual_sync(integration_id, user["id"])
    if not accepted:
        if eligible_at is None:
            raise HTTPException(status_code=404, detail="Integration not found")
        return {"status": "skipped", "retry_at": eligible_at}
    try:
        result = await calendar_service.sync_integration(integration, force_remote_read=True)
        return {"status": "synced", **result}
    except Exception as exc:
        LOG.exception("Manual sync failed integration=%s", integration_id)
        raise HTTPException(status_code=502, detail="Could not sync this schedule right now") from exc


@router.delete("/{integration_id}", dependencies=[Depends(require_csrf)])
async def delete_integration(integration_id: str, request: Request, user: dict = Depends(current_user)) -> dict:
    repository, calendar_service = _services(request)
    integration = repository.get_integration(integration_id, user["id"])
    if not integration:
        raise HTTPException(status_code=404, detail="Integration not found")
    try:
        calendar_deleted = await asyncio.to_thread(calendar_service.delete_integration, integration)
    except Exception as exc:
        LOG.exception("Could not delete app-managed Google Calendar")
        raise HTTPException(status_code=502, detail="Could not delete the dedicated Google Calendar") from exc
    return {"deleted": True, "calendar_deleted": calendar_deleted}
