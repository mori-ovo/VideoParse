from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import FileResponse, Response

from app.services.proxy_service import proxy_service
from app.services.storage_service import storage_service
from app.services.task_service import task_service

router = APIRouter(prefix="/files", tags=["files"])

FILE_NOT_FOUND_DETAIL = "文件不存在或已被清理。"


async def _build_task_proxy_file_response(
    request: Request,
    file_id: str,
    *,
    as_attachment: bool,
) -> Response | None:
    task = await task_service.get_task_by_file_id(file_id)
    if task is None or task.result is None:
        return None

    response = await proxy_service.build_proxy_response(
        task_id=task.task_id,
        kind="single",
        request=request,
    )
    file_name = task.result.file_name or f"{file_id}.mp4"
    disposition = "attachment" if as_attachment else "inline"
    response.headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(file_name)}"
    return response


@router.get("/{file_id}/download", summary="下载任务产物")
async def download_file(request: Request, file_id: str) -> Response:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is not None:
        return FileResponse(
            path=stored_file.path,
            media_type=stored_file.content_type,
            filename=stored_file.file_name,
            content_disposition_type="attachment",
        )

    proxy_response = await _build_task_proxy_file_response(
        request=request,
        file_id=file_id,
        as_attachment=True,
    )
    if proxy_response is not None:
        return proxy_response

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=FILE_NOT_FOUND_DETAIL,
    )


@router.get("/{file_id}.{extension}", summary="播放或复制短文件直链")
async def stream_file_short(request: Request, file_id: str, extension: str) -> Response:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is not None:
        return FileResponse(
            path=stored_file.path,
            media_type=stored_file.content_type,
            content_disposition_type="inline",
        )

    proxy_response = await _build_task_proxy_file_response(
        request=request,
        file_id=file_id,
        as_attachment=False,
    )
    if proxy_response is not None:
        return proxy_response

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=FILE_NOT_FOUND_DETAIL,
    )


@router.get("/{file_id}", summary="获取可播放文件地址")
async def open_file(request: Request, file_id: str) -> Response:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is not None:
        return FileResponse(
            path=stored_file.path,
            media_type=stored_file.content_type,
            content_disposition_type="inline",
        )

    proxy_response = await _build_task_proxy_file_response(
        request=request,
        file_id=file_id,
        as_attachment=False,
    )
    if proxy_response is not None:
        return proxy_response

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=FILE_NOT_FOUND_DETAIL,
    )


@router.get("/{file_id}/{file_name:path}", summary="播放或复制文件直链")
async def stream_file(request: Request, file_id: str, file_name: str) -> Response:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is not None:
        return FileResponse(
            path=stored_file.path,
            media_type=stored_file.content_type,
            content_disposition_type="inline",
        )

    proxy_response = await _build_task_proxy_file_response(
        request=request,
        file_id=file_id,
        as_attachment=False,
    )
    if proxy_response is not None:
        return proxy_response

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=FILE_NOT_FOUND_DETAIL,
    )
