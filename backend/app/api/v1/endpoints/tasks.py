from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse

from app.schemas.task import TaskRecord, TaskResult
from app.services.proxy_service import proxy_service
from app.services.task_service import task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("/{task_id}", response_model=TaskRecord, summary="获取任务状态")
async def get_task(task_id: str) -> TaskRecord:
    task = await task_service.get_task(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务不存在。",
        )
    return task


@router.get("/{task_id}/result", response_model=TaskResult, summary="获取任务结果")
async def get_task_result(task_id: str) -> TaskResult:
    result = await task_service.get_result(task_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="任务结果尚未生成。",
        )
    return result


@router.api_route(
    "/{task_id}/redirect",
    methods=["GET", "HEAD"],
    summary="获取可刷新的媒体重定向地址",
)
async def redirect_task_media(
    task_id: str,
    kind: str = Query(default="single", pattern="^(single|video|audio)$"),
) -> RedirectResponse:
    target_url = await task_service.resolve_redirect_url(task_id, kind)
    return RedirectResponse(url=target_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)


@router.api_route(
    "/{task_id}/proxy",
    methods=["GET", "HEAD"],
    summary="获取项目生成的稳定媒体代理地址",
)
async def proxy_task_media(
    request: Request,
    task_id: str,
    kind: str = Query(default="single", pattern="^(single|video|audio)$"),
) -> object:
    return await proxy_service.build_proxy_response(task_id=task_id, kind=kind, request=request)


@router.api_route(
    "/{task_id}/proxy/{file_name:path}",
    methods=["GET", "HEAD"],
    summary="获取带扩展名的媒体代理地址",
)
async def proxy_task_media_with_name(
    request: Request,
    task_id: str,
    file_name: str,
    kind: str = Query(default="single", pattern="^(single|video|audio)$"),
) -> object:
    return await proxy_service.build_proxy_response(task_id=task_id, kind=kind, request=request)
