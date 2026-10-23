from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.auth import current_user, require_csrf
from app.db.repository import Repository

router = APIRouter(prefix="/api", tags=["notifications"])


def _repository(request: Request) -> Repository:
    return request.app.state.repository


@router.get("/notifications")
async def notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: dict = Depends(current_user),
) -> dict:
    records = _repository(request).notifications(
        user["id"], unread_only=unread_only, limit=limit + 1, offset=offset
    )
    return {"items": records[:limit], "has_more": len(records) > limit}


@router.post("/notifications/read-all", dependencies=[Depends(require_csrf)])
async def mark_all_read(request: Request, user: dict = Depends(current_user)) -> dict[str, int]:
    return {"marked_read": _repository(request).mark_all_notifications_read(user["id"])}


@router.post("/notifications/{notification_id}/read", dependencies=[Depends(require_csrf)])
async def mark_read(notification_id: str, request: Request, user: dict = Depends(current_user)) -> dict[str, bool]:
    if not _repository(request).mark_notification_read(notification_id, user["id"]):
        raise HTTPException(status_code=404, detail="Unread notification not found")
    return {"read": True}


@router.get("/changelog")
async def changelog(request: Request, user: dict = Depends(current_user)) -> list[dict]:
    return _repository(request).list_changelog(user["id"])
