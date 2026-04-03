from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from app.services.storage_service import storage_service

router = APIRouter(prefix="/files", tags=["files"])


@router.get("/{file_id}/download", summary="下载任务产物")
async def download_file(file_id: str) -> FileResponse:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在或已被清理。",
        )

    return FileResponse(
        path=stored_file.path,
        media_type=stored_file.content_type,
        filename=stored_file.file_name,
        content_disposition_type="attachment",
    )


@router.get("/{file_id}.{extension}", summary="播放或复制短文件直链")
async def stream_file_short(file_id: str, extension: str) -> FileResponse:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在或已被清理。",
        )

    return FileResponse(
        path=stored_file.path,
        media_type=stored_file.content_type,
        content_disposition_type="inline",
    )


@router.get("/{file_id}", summary="获取可播放文件地址")
async def open_file(file_id: str) -> FileResponse:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在或已被清理。",
        )

    return FileResponse(
        path=stored_file.path,
        media_type=stored_file.content_type,
        content_disposition_type="inline",
    )


@router.get("/{file_id}/{file_name:path}", summary="播放或复制文件直链")
async def stream_file(file_id: str, file_name: str) -> FileResponse:
    stored_file = await storage_service.get_file(file_id)
    if stored_file is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="文件不存在或已被清理。",
        )

    return FileResponse(
        path=stored_file.path,
        media_type=stored_file.content_type,
        content_disposition_type="inline",
    )
