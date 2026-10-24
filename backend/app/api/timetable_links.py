from fastapi import APIRouter, Depends, Query, Request

from app.api.auth import current_user
from app.db.repository import Repository

router = APIRouter(prefix="/api/timetable-links", tags=["timetable links"])


@router.get("")
async def list_timetable_links(
    request: Request,
    search: str = Query(default="", max_length=200),
    limit: int = Query(default=1000, ge=1, le=2000),
    _user: dict = Depends(current_user),
) -> list[dict]:
    repository: Repository = request.app.state.repository
    return repository.timetable_links(query=search, limit=limit)
