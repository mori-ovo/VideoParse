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
        note="当前默认使用自动模式：有单文件直链就直接返回；长分离流优先生成稳定单链接；其余情况再自动下载并合流。",
    )
