import asyncio

from fastapi import APIRouter, status

from app.schemas.parse import ParseAcceptedResponse, ParseRequest
from app.services.task_service import task_service

router = APIRouter(prefix="/parse", tags=["parse"])


@router.post("", response_model=ParseAcceptedResponse, status_code=status.HTTP_202_ACCEPTED)
async def create_parse_task(payload: ParseRequest) -> ParseAcceptedResponse:
    task = await task_service.create_task(payload)
    asyncio.create_task(task_service.run_download_pipeline(task.task_id))
    return ParseAcceptedResponse(
        task=task,
        note="默认使用直链优先模式以降低 1C1G 服务器压力；只有显式切换到下载模式时，才会进行真实下载和 ffmpeg 合流。",
    )
