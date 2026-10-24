from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.auth import current_user, require_csrf
from app.db.repository import Repository

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


class FeedbackRequest(BaseModel):
    body: str = Field(min_length=1, max_length=3000)


def _repository(request: Request) -> Repository:
    return request.app.state.repository


@router.get("")
async def get_feedback(request: Request, user: dict = Depends(current_user)) -> dict | None:
    return _repository(request).get_feedback(user["id"])


@router.put("", dependencies=[Depends(require_csrf)])
async def save_feedback(
    payload: FeedbackRequest, request: Request, user: dict = Depends(current_user)
) -> dict:
    return _repository(request).save_feedback(user["id"], payload.body)


@router.delete("", dependencies=[Depends(require_csrf)])
async def delete_feedback(request: Request, user: dict = Depends(current_user)) -> dict[str, bool]:
    _repository(request).delete_feedback(user["id"])
    return {"deleted": True}
