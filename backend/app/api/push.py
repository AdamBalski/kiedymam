from hashlib import sha256
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_csrf

router = APIRouter(prefix="/api/push", tags=["push"])
ALLOWED_PUSH_HOSTS = (
    "fcm.googleapis.com", "push.services.mozilla.com", "push.apple.com", "notify.windows.com"
)


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=16, max_length=256)
    auth: str = Field(min_length=8, max_length=128)


class SubscriptionRequest(BaseModel):
    endpoint: str = Field(min_length=20, max_length=4096)
    keys: PushKeys


@router.get("/config")
async def push_config(request: Request, user: dict = Depends(current_user)) -> dict:
    del user
    service = request.app.state.push_service
    return {"enabled": service.configured, "public_key": service.public_key if service.configured else None}


@router.get("/subscriptions")
async def list_subscriptions(request: Request, user: dict = Depends(current_user)) -> list[dict]:
    repository, crypto = request.app.state.repository, request.app.state.crypto
    return [{
        "id": item["id"], "created_at": item["created_at"], "user_agent": item["user_agent"],
        "endpoint_hash": sha256(item["subscription"].get("endpoint", "").encode("utf-8")).hexdigest(),
    } for item in repository.push_subscriptions(user["id"], crypto)]


@router.post("/subscriptions", dependencies=[Depends(require_csrf)])
async def create_subscription(
    payload: SubscriptionRequest, request: Request, user: dict = Depends(current_user)
) -> dict:
    service = request.app.state.push_service
    if not service.configured:
        raise HTTPException(status_code=503, detail="Push notifications are not configured")
    try:
        parts = urlsplit(payload.endpoint)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid push endpoint") from exc
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not any(
        host == allowed or host.endswith("." + allowed) for allowed in ALLOWED_PUSH_HOSTS
    ):
        raise HTTPException(status_code=400, detail="Unsupported push service endpoint")
    subscription_id = request.app.state.repository.save_push_subscription(
        user["id"], payload.model_dump(), request.headers.get("user-agent", ""), request.app.state.crypto
    )
    return {"id": subscription_id, "saved": True}


@router.delete("/subscriptions/{subscription_id}", dependencies=[Depends(require_csrf)])
async def delete_subscription(
    subscription_id: str, request: Request, user: dict = Depends(current_user)
) -> dict:
    if not request.app.state.repository.delete_push_subscription(subscription_id, user["id"]):
        raise HTTPException(status_code=404, detail="Device subscription not found")
    return {"deleted": True}
