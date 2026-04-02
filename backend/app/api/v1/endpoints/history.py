from fastapi import APIRouter, Query

from app.schemas.task import TaskRecord
from app.services.task_service import task_service

router = APIRouter(prefix="/history", tags=["history"])


@router.get("", response_model=list[TaskRecord], summary="获取历史任务")
async def get_history(limit: int = Query(default=20, ge=1, le=100)) -> list[TaskRecord]:
    return await task_service.list_tasks(limit=limit)

