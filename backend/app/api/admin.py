from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_csrf

router = APIRouter(prefix="/api/admin", tags=["admin"])


def admin_user(request: Request, user: dict = Depends(current_user)) -> dict:
    expected = request.app.state.settings.admin_email
    if not expected or user.get("email", "").strip().casefold() != expected:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


class TestPushRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    message: str = Field(min_length=1, max_length=256)


@router.post("/send-push", dependencies=[Depends(require_csrf)])
async def send_test_push(
    payload: TestPushRequest, request: Request, admin: dict = Depends(admin_user),
) -> dict:
    normalized_email = payload.email.strip().casefold()
    if not normalized_email:
        raise HTTPException(status_code=422, detail="Enter a recipient email")
    push_service = request.app.state.push_service
    if not push_service.configured:
        raise HTTPException(status_code=503, detail="Web Push is not configured")

    now = time.monotonic()
    last = request.app.state.admin_test_push_last.get(admin["id"], 0)
    if now - last < 60:
        wait = max(1, int(60 - (now - last)))
        raise HTTPException(
            status_code=429,
            detail="Wait before sending another test push",
            headers={"Retry-After": str(wait)},
        )
    request.app.state.admin_test_push_last[admin["id"]] = now

    recipient = request.app.state.repository.get_user_by_email(normalized_email)
    if not recipient:
        raise HTTPException(status_code=404, detail="No KiedyMam account uses that email address")
    result = await asyncio.to_thread(push_service.send_notifications, [{
        "id": str(uuid.uuid4()),
        "user_id": recipient["id"],
        "message": payload.message,
    }])
    return {"recipient_email": normalized_email, **result}
