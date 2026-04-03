import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status

from app.core.config import settings
from app.schemas.parse import ParseRequest
from app.schemas.task import DeliveryMode, Platform, ResultType, TaskRecord, TaskResult, TaskStatus
from app.services.downloader_service import (
    DownloadProgressEvent,
    DownloaderExecutionError,
    DownloaderUnavailableError,
    downloader_service,
)
from app.services.storage_service import storage_service

logger = logging.getLogger(__name__)

PLATFORM_PATTERNS: tuple[tuple[Platform, re.Pattern[str]], ...] = (
    (Platform.BILIBILI, re.compile(r"(bilibili\.com|b23\.tv)", re.IGNORECASE)),
    (Platform.DOUYIN, re.compile(r"(douyin\.com|iesdouyin\.com)", re.IGNORECASE)),
    (Platform.TWITTER, re.compile(r"(twitter\.com|x\.com)", re.IGNORECASE)),
    (Platform.YOUTUBE, re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)),
    (Platform.REDDIT, re.compile(r"(reddit\.com|redd\.it)", re.IGNORECASE)),
)


class TaskService:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create_task(self, payload: ParseRequest) -> TaskRecord:
        source_url = str(payload.url)
        platform = self.detect_platform(source_url)
        task_id = uuid4().hex
        now = datetime.now(timezone.utc)

        task = TaskRecord(
            task_id=task_id,
            source_url=source_url,
            platform=platform,
            delivery_mode=payload.delivery_mode,
            status=TaskStatus.PENDING,
            progress=0,
            title=f"{platform.value} parse task",
            message="任务已创建，等待开始解析。",
            requires_merge=platform in {Platform.BILIBILI, Platform.TWITTER, Platform.YOUTUBE},
            direct_playable=False,
            created_at=now,
            updated_at=now,
        )

        async with self._lock:
            self._tasks[task_id] = task
        return task

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            return self._tasks.get(task_id)

    async def list_tasks(self, limit: int = 20) -> list[TaskRecord]:
        async with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
        return tasks[:limit]

    async def get_result(self, task_id: str) -> TaskResult | None:
        task = await self.get_task(task_id)
        if task is None:
            return None
        return task.result

    async def run_download_pipeline(self, task_id: str) -> None:
        task = await self.get_task(task_id)
        if task is None:
            return

        try:
            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.PARSING,
                progress=10,
                message="正在通过 yt-dlp 提取视频信息。",
            )

            metadata = await downloader_service.extract_metadata(task.source_url)
            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.PARSING,
                progress=30,
                message="视频信息提取完成，正在判断是否需要自动合流。",
                extra_updates={
                    "title": metadata.title,
                    "requires_merge": metadata.requires_merge,
                    "direct_playable": metadata.direct_playable,
                    "uploader": metadata.uploader,
                    "duration": metadata.duration,
                    "thumbnail": metadata.thumbnail,
                    "extractor": metadata.extractor,
                },
            )

            task = await self.get_task(task_id)
            if task is None:
                return

            if (
                task.delivery_mode == DeliveryMode.DOWNLOAD
                and metadata.direct_url
                and metadata.extractor in {"iiilab", "fxtwitter"}
            ):
                result = self._build_direct_result(task_id=task_id, metadata=metadata)
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message="已通过第三方兜底生成本站视频链接。你现在可以复制直链，或直接下载视频。",
                    result=result,
                )
                return

            if task.delivery_mode == DeliveryMode.DIRECT:
                result = self._build_direct_result(task_id=task_id, metadata=metadata)
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message=self._build_direct_message(result.result_type),
                    result=result,
                )
                return

            if task.delivery_mode == DeliveryMode.AUTO and metadata.direct_url:
                result = self._build_direct_result(task_id=task_id, metadata=metadata)
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message="已生成可分享的视频直链。你现在可以复制直链，或直接下载视频。",
                    result=result,
                )
                return

            availability = downloader_service.availability()
            if metadata.requires_merge and not availability.ffmpeg_available:
                raise DownloaderUnavailableError(
                    "当前资源需要音视频合流，但服务器未找到 ffmpeg。请先安装 ffmpeg 或配置 FFMPEG_LOCATION。"
                )

            loop = asyncio.get_running_loop()

            def progress_callback(event: DownloadProgressEvent) -> None:
                future = asyncio.run_coroutine_threadsafe(
                    self._apply_progress_event(task_id, event),
                    loop,
                )
                future.add_done_callback(lambda _: None)

            downloaded_media = await downloader_service.download(
                task_id=task_id,
                url=task.source_url,
                progress_callback=progress_callback,
            )

            if downloaded_media.requires_merge:
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.MERGING,
                    progress=90,
                    message="源站是分离流，已自动下载并通过 ffmpeg 合流。",
                )

            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.UPLOADING,
                progress=95,
                message="正在注册最终视频文件并生成访问地址。",
                extra_updates={
                    "title": downloaded_media.title,
                    "requires_merge": downloaded_media.requires_merge,
                    "direct_playable": metadata.direct_playable,
                    "uploader": downloaded_media.uploader,
                    "duration": downloaded_media.duration,
                    "thumbnail": downloaded_media.thumbnail,
                    "extractor": downloaded_media.extractor,
                },
            )

            result = await storage_service.register_downloaded_file(downloaded_media.file_path)
            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.SUCCESS,
                progress=100,
                message="已生成单文件视频。你现在可以复制视频直链，或直接下载视频。",
                result=result,
            )
        except (DownloaderUnavailableError, DownloaderExecutionError) as exc:
            logger.exception("download pipeline failed for task_id=%s", task_id)
            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.FAILED,
                progress=100,
                message="任务失败。",
                error_message=str(exc),
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("unexpected error for task_id=%s", task_id)
            await self._update_task(
                task_id=task_id,
                status_value=TaskStatus.FAILED,
                progress=100,
                message="任务失败。",
                error_message=f"出现未预期错误：{exc}",
            )

    async def _apply_progress_event(self, task_id: str, event: DownloadProgressEvent) -> None:
        status_value = TaskStatus.DOWNLOADING
        if event.status == "merging":
            status_value = TaskStatus.MERGING
        elif event.status == "uploading":
            status_value = TaskStatus.UPLOADING

        await self._update_task(
            task_id=task_id,
            status_value=status_value,
            progress=event.progress,
            message=event.message,
        )

    async def resolve_redirect_url(self, task_id: str, kind: str) -> str:
        return await self.resolve_media_url(task_id=task_id, kind=kind, force_refresh=True)

    async def resolve_media_url(
        self,
        task_id: str,
        kind: str,
        force_refresh: bool = False,
    ) -> str:
        task = await self.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="任务不存在。",
            )

        if not force_refresh:
            cached_url = self._get_cached_result_url(task, kind)
            if cached_url:
                return cached_url

        metadata = await downloader_service.extract_metadata(task.source_url)

        if kind == "single":
            if metadata.direct_url:
                return metadata.direct_url
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前源站没有单文件直链，请改用自动模式生成单文件视频地址。",
            )

        if kind == "video":
            if metadata.video_url:
                return metadata.video_url
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前任务没有可用的视频流直链。",
            )

        if kind == "audio":
            if metadata.audio_url:
                return metadata.audio_url
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前任务没有可用的音频流直链。",
            )

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="kind 只支持 single、video、audio。",
        )

    def _get_cached_result_url(self, task: TaskRecord, kind: str) -> str | None:
        if task.result is None:
            return None

        if kind == "single":
            return task.result.direct_url
        if kind == "video":
            return task.result.video_url
        if kind == "audio":
            return task.result.audio_url
        return None

    def _build_direct_message(self, result_type: ResultType) -> str:
        if result_type == ResultType.DIRECT:
            return "已生成可分享的视频直链。你现在可以复制直链，或直接下载视频。"
        return "当前只能拿到分离流地址。若需要单文件，请使用自动模式。"

    def _build_direct_result(self, task_id: str, metadata: Any) -> TaskResult:
        created_at = datetime.now(timezone.utc)
        redirect_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/redirect"
        proxy_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/proxy"

        if metadata.direct_url:
            proxy_url = f"{proxy_base_url}?kind=single"
            return TaskResult(
                result_type=ResultType.DIRECT,
                play_url=proxy_url,
                download_url=proxy_url,
                direct_url=metadata.direct_url,
                redirect_url=f"{redirect_base_url}?kind=single",
                proxy_url=proxy_url,
                created_at=created_at,
                expires_note="建议优先使用项目生成的 play_url 或 proxy_url。源站直链通常带时效。",
            )

        if metadata.video_url or metadata.audio_url:
            return TaskResult(
                result_type=ResultType.SPLIT_STREAMS,
                direct_url=None,
                video_url=metadata.video_url,
                video_redirect_url=f"{redirect_base_url}?kind=video" if metadata.video_url else None,
                video_proxy_url=f"{proxy_base_url}?kind=video" if metadata.video_url else None,
                audio_url=metadata.audio_url,
                audio_redirect_url=f"{redirect_base_url}?kind=audio" if metadata.audio_url else None,
                audio_proxy_url=f"{proxy_base_url}?kind=audio" if metadata.audio_url else None,
                created_at=created_at,
                expires_note="当前只有分离流地址。自动模式会下载并合成为单文件。",
            )

        raise DownloaderExecutionError("未能通过 yt-dlp 提取到可用的媒体地址。")

    async def _update_task(
        self,
        task_id: str,
        status_value: TaskStatus,
        progress: int,
        message: str,
        result: TaskResult | None = None,
        error_message: str | None = None,
        extra_updates: dict[str, Any] | None = None,
    ) -> TaskRecord:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="任务不存在。",
                )

            updates: dict[str, Any] = {
                "status": status_value,
                "progress": progress,
                "message": message,
                "result": result if result is not None else task.result,
                "error_message": error_message,
                "updated_at": datetime.now(timezone.utc),
            }
            if extra_updates:
                updates.update(extra_updates)

            updated_task = task.model_copy(update=updates)
            self._tasks[task_id] = updated_task
            return updated_task

    def detect_platform(self, source_url: str) -> Platform:
        for platform, pattern in PLATFORM_PATTERNS:
            if pattern.search(source_url):
                return platform

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前只支持 bilibili、douyin、twitter/x、youtube、reddit 链接。",
        )


task_service = TaskService()
