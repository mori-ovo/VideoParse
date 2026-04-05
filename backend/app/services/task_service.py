import asyncio
import json
import logging
import mimetypes
import re
import secrets
import string
from datetime import datetime, timezone
from pathlib import Path
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
    ExtractedMedia,
    downloader_service,
)
from app.services.storage_service import storage_service
from app.utils.path import build_public_file_name

logger = logging.getLogger(__name__)

PLATFORM_PATTERNS: tuple[tuple[Platform, re.Pattern[str]], ...] = (
    (Platform.BILIBILI, re.compile(r"(bilibili\.com|b23\.tv)", re.IGNORECASE)),
    (Platform.TWITTER, re.compile(r"(twitter\.com|x\.com)", re.IGNORECASE)),
    (Platform.YOUTUBE, re.compile(r"(youtube\.com|youtu\.be)", re.IGNORECASE)),
    (Platform.REDDIT, re.compile(r"(reddit\.com|redd\.it)", re.IGNORECASE)),
    (Platform.IWARA, re.compile(r"(iwara\.tv)", re.IGNORECASE)),
)
PURE_BILIBILI_BV_PATTERN = re.compile(r"^(?P<bvid>BV[0-9A-Za-z]{10})$", re.IGNORECASE)

TERMINAL_TASK_STATUSES = {TaskStatus.SUCCESS, TaskStatus.FAILED}
FFMPEG_MISSING_MESSAGE = (
    "当前资源需要音视频合流，但服务器未找到 ffmpeg。请先安装 ffmpeg 或配置 FFMPEG_LOCATION。"
)
TASK_INDEX_PERSIST_DEBOUNCE_SECONDS = 0.5


class TaskService:
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = self._load_tasks()
        self._file_id_index = self._build_file_id_index(self._tasks)
        self._lock = asyncio.Lock()
        self._persist_dirty = False
        self._persist_task: asyncio.Task[None] | None = None

    async def recover_tasks(self) -> None:
        async with self._lock:
            if not self._tasks:
                return

            now = datetime.now(timezone.utc)
            changed = False
            for task_id, task in list(self._tasks.items()):
                if task.status in TERMINAL_TASK_STATUSES:
                    continue

                self._tasks[task_id] = task.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "progress": 100,
                        "message": "服务已重启，未完成任务已标记为失败，请重新提交解析。",
                        "error_message": task.error_message or "后端已重启，原任务执行被中断。",
                        "updated_at": now,
                    }
                )
                changed = True

            if changed:
                self._persist_tasks_unlocked()

    async def stop(self) -> None:
        await self._flush_pending_persist()
        if self._persist_task is not None:
            self._persist_task.cancel()
            await asyncio.gather(self._persist_task, return_exceptions=True)
            self._persist_task = None

    async def create_task(self, payload: ParseRequest) -> TaskRecord:
        source_url = self.normalize_source_url(str(payload.url))
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
            self._persist_tasks_unlocked()
        return task

    def normalize_source_url(self, source_url: str) -> str:
        normalized = source_url.strip()
        match = PURE_BILIBILI_BV_PATTERN.fullmatch(normalized)
        if match is None:
            return normalized
        return f"https://www.bilibili.com/video/{match.group('bvid')}"

    async def get_task(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None

            migrated_task = self._migrate_task_result_links(task)
            if migrated_task is not task:
                self._tasks[task_id] = migrated_task
                self._update_file_id_index(previous_task=task, current_task=migrated_task)
                self._persist_tasks_unlocked()
                return migrated_task
            return task

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
                result = self._build_direct_result(
                    task_id=task_id,
                    metadata=metadata,
                    platform=task.platform,
                )
                result = self._promote_proxy_task_result(
                    result=result,
                    title=metadata.title,
                    extension=metadata.direct_ext,
                    expires_note="已生成本站短链。播放时会优先复用源站单文件直链，失效后再按需刷新。",
                )
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message="已通过第三方兜底生成本站视频链接。你现在可以复制直链，或直接下载视频。",
                    result=result,
                )
                return

            if task.delivery_mode == DeliveryMode.DIRECT:
                result = self._build_direct_result(
                    task_id=task_id,
                    metadata=metadata,
                    platform=task.platform,
                )
                result = self._promote_proxy_task_result(
                    result=result,
                    title=metadata.title,
                    extension=metadata.direct_ext,
                    expires_note="已生成本站短链。播放时会优先复用源站单文件直链，失效后再按需刷新。",
                )
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message=self._build_direct_message(result.result_type),
                    result=result,
                )
                return

            if task.delivery_mode == DeliveryMode.AUTO and metadata.direct_url:
                result = self._build_direct_result(
                    task_id=task_id,
                    metadata=metadata,
                    platform=task.platform,
                )
                result = self._promote_proxy_task_result(
                    result=result,
                    title=metadata.title,
                    extension=metadata.direct_ext,
                    expires_note="已生成本站短链。播放时会优先复用源站单文件直链，失效后再按需刷新。",
                )
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message="已生成可分享的视频直链。你现在可以复制直链，或直接下载视频。",
                    result=result,
                )
                return

            availability = downloader_service.availability()
            if task.delivery_mode == DeliveryMode.AUTO and self._should_use_lazy_stream(metadata):
                if not availability.ffmpeg_available:
                    raise DownloaderUnavailableError(FFMPEG_MISSING_MESSAGE)

                # 长视频在 auto 模式下优先返回本站单链接，让前端尽快拿到可用地址。
                result = self._build_lazy_stream_result(
                    task_id=task_id,
                    metadata=metadata,
                    platform=task.platform,
                )
                result = self._promote_proxy_task_result(
                    result=result,
                    title=metadata.title,
                    extension=settings.merge_output_format,
                    expires_note="长视频已生成本站短链。首次播放时会按需实时合流。",
                )
                await self._update_task(
                    task_id=task_id,
                    status_value=TaskStatus.SUCCESS,
                    progress=100,
                    message="长视频已生成稳定单链接，可直接复制或下载。",
                    result=result,
                )
                return

            if metadata.requires_merge and not availability.ffmpeg_available:
                raise DownloaderUnavailableError(FFMPEG_MISSING_MESSAGE)

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

        metadata = await downloader_service.extract_metadata(
            task.source_url,
            force_refresh=force_refresh,
        )

        if kind == "single":
            if metadata.direct_url:
                return metadata.direct_url
            if task.result is not None and task.result.proxy_url and task.result.video_url and task.result.audio_url:
                # 对长分离流任务，single 实际指向本站的合流代理地址。
                return task.result.proxy_url
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

    def _should_use_lazy_stream(self, metadata: ExtractedMedia) -> bool:
        # 只有“没有单文件直链 + 有音视频分离流 + 时长超过阈值”才走懒合流。
        if metadata.direct_url:
            return False
        if not (metadata.video_url and metadata.audio_url):
            return False
        if metadata.duration is None:
            return False
        return metadata.duration >= settings.lazy_stream_min_duration_seconds

    def _generate_public_file_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(12))

    def _normalize_media_extension(self, extension: str | None, default: str = "mp4") -> str:
        normalized = (extension or "").strip().lower().lstrip(".")
        return normalized or default

    def _build_result_file_name(self, title: str, extension: str | None) -> str:
        normalized_extension = self._normalize_media_extension(extension)
        raw_file_name = f"{title or 'video'}.{normalized_extension}"
        return build_public_file_name(raw_file_name, fallback_stem="video")

    def _build_public_file_links(self, file_id: str, file_name: str) -> tuple[str, str]:
        play_url = storage_service.build_stream_url(file_id, file_name)
        download_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/files/{file_id}/download"
        return play_url, download_url

    def _build_task_proxy_file_result(
        self,
        *,
        title: str,
        extension: str | None,
        created_at: datetime,
        redirect_url: str,
        proxy_url: str,
        expires_note: str,
        video_url: str | None = None,
        video_redirect_url: str | None = None,
        video_proxy_url: str | None = None,
        audio_url: str | None = None,
        audio_redirect_url: str | None = None,
        audio_proxy_url: str | None = None,
    ) -> TaskResult:
        # 这类结果本质上仍然是任务代理流，但对外暴露成 /files/{12位}.mp4，
        # 这样既保留之前的短链接格式，也不用等待整段视频先下载完成。
        file_id = self._generate_public_file_id()
        file_name = self._build_result_file_name(title=title, extension=extension)
        play_url, download_url = self._build_public_file_links(file_id=file_id, file_name=file_name)
        content_type = mimetypes.guess_type(file_name)[0] or "video/mp4"
        return TaskResult(
            result_type=ResultType.DIRECT,
            file_id=file_id,
            file_name=file_name,
            content_type=content_type,
            play_url=play_url,
            download_url=download_url,
            direct_url=play_url,
            redirect_url=redirect_url,
            proxy_url=proxy_url,
            video_url=video_url,
            video_redirect_url=video_redirect_url,
            video_proxy_url=video_proxy_url,
            audio_url=audio_url,
            audio_redirect_url=audio_redirect_url,
            audio_proxy_url=audio_proxy_url,
            created_at=created_at,
            expires_note=expires_note,
        )

    def _promote_proxy_task_result(
        self,
        *,
        result: TaskResult,
        title: str,
        extension: str | None,
        expires_note: str,
    ) -> TaskResult:
        if result.result_type != ResultType.DIRECT:
            return result
        if result.file_id or not result.redirect_url:
            return result

        return self._build_task_proxy_file_result(
            title=title,
            extension=extension,
            created_at=result.created_at,
            redirect_url=result.redirect_url,
            proxy_url=result.proxy_url or result.redirect_url,
            expires_note=expires_note,
            video_url=result.video_url,
            video_redirect_url=result.video_redirect_url,
            video_proxy_url=result.video_proxy_url,
            audio_url=result.audio_url,
            audio_redirect_url=result.audio_redirect_url,
            audio_proxy_url=result.audio_proxy_url,
        )

    def _migrate_iwara_short_file_result(self, task: TaskRecord, result: TaskResult) -> TaskResult:
        existing_extension = Path(result.file_name).suffix if result.file_name else ""
        extension = existing_extension.lstrip(".") or settings.merge_output_format
        file_id = result.file_id or self._generate_public_file_id()
        file_name = result.file_name or self._build_result_file_name(task.title, extension)
        play_url, download_url = self._build_public_file_links(file_id=file_id, file_name=file_name)
        content_type = result.content_type or mimetypes.guess_type(file_name)[0] or "video/mp4"

        updates: dict[str, str] = {}
        if result.file_id != file_id:
            updates["file_id"] = file_id
        if result.file_name != file_name:
            updates["file_name"] = file_name
        if result.content_type != content_type:
            updates["content_type"] = content_type
        if result.play_url != play_url:
            updates["play_url"] = play_url
        if result.download_url != download_url:
            updates["download_url"] = download_url
        if result.direct_url != play_url:
            updates["direct_url"] = play_url

        if not updates:
            return result
        return result.model_copy(update=updates)

    async def get_task_by_file_id(self, file_id: str) -> TaskRecord | None:
        async with self._lock:
            task_id = self._file_id_index.get(file_id)
            if task_id is None:
                return None
            return self._tasks.get(task_id)

    def _build_direct_result(
        self,
        task_id: str,
        metadata: ExtractedMedia,
        platform: Platform,
    ) -> TaskResult:
        created_at = datetime.now(timezone.utc)
        redirect_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/redirect"
        proxy_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/proxy"

        if metadata.direct_url:
            redirect_url = f"{redirect_base_url}?kind=single"
            proxy_url = f"{proxy_base_url}?kind=single"
            play_url = redirect_url
            download_url = redirect_url
            direct_url = metadata.direct_url

            if platform == Platform.IWARA:
                return self._build_task_proxy_file_result(
                    title=metadata.title,
                    extension=metadata.direct_ext,
                    created_at=created_at,
                    redirect_url=redirect_url,
                    proxy_url=proxy_url,
                    expires_note="Iwara 使用本站生成的 12 位短链接，实际播放时由后端代理源站直链。",
                )

            return TaskResult(
                result_type=ResultType.DIRECT,
                play_url=play_url,
                download_url=download_url,
                direct_url=direct_url,
                redirect_url=redirect_url,
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

    def _build_lazy_stream_result(
        self,
        task_id: str,
        metadata: ExtractedMedia,
        platform: Platform,
    ) -> TaskResult:
        created_at = datetime.now(timezone.utc)
        proxy_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/proxy?kind=single"
        redirect_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/redirect"
        proxy_base_url = f"{settings.api_public_origin}{settings.api_v1_prefix}/tasks/{task_id}/proxy"
        if platform == Platform.IWARA:
            return self._build_task_proxy_file_result(
                title=metadata.title,
                extension=settings.merge_output_format,
                created_at=created_at,
                redirect_url=proxy_url,
                proxy_url=proxy_url,
                expires_note="Iwara 长视频使用本站生成的 12 位短链接，播放时会按需合流。",
                video_url=metadata.video_url,
                video_redirect_url=f"{redirect_base_url}?kind=video" if metadata.video_url else None,
                video_proxy_url=f"{proxy_base_url}?kind=video" if metadata.video_url else None,
                audio_url=metadata.audio_url,
                audio_redirect_url=f"{redirect_base_url}?kind=audio" if metadata.audio_url else None,
                audio_proxy_url=f"{proxy_base_url}?kind=audio" if metadata.audio_url else None,
            )
        # 对前端仍然伪装成 direct 结果，这样现有按钮逻辑不用改。
        return TaskResult(
            result_type=ResultType.DIRECT,
            play_url=proxy_url,
            download_url=proxy_url,
            direct_url=proxy_url,
            redirect_url=proxy_url,
            proxy_url=proxy_url,
            video_url=metadata.video_url,
            video_redirect_url=f"{redirect_base_url}?kind=video" if metadata.video_url else None,
            video_proxy_url=f"{proxy_base_url}?kind=video" if metadata.video_url else None,
            audio_url=metadata.audio_url,
            audio_redirect_url=f"{redirect_base_url}?kind=audio" if metadata.audio_url else None,
            audio_proxy_url=f"{proxy_base_url}?kind=audio" if metadata.audio_url else None,
            created_at=created_at,
            expires_note="长视频使用本站生成的单链接，播放时会按需合流。",
        )

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
            self._update_file_id_index(previous_task=task, current_task=updated_task)
            if self._should_persist_update_immediately(
                previous_task=task,
                updated_task=updated_task,
                status_value=status_value,
                result=result,
                error_message=error_message,
            ):
                self._persist_tasks_unlocked()
            else:
                self._mark_persist_dirty_unlocked()
            return updated_task

    def _load_tasks(self) -> dict[str, TaskRecord]:
        index_path = settings.task_index_path
        if not index_path.exists():
            return {}

        try:
            raw_data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("failed to load task index: %s", index_path)
            return {}

        if not isinstance(raw_data, list):
            logger.warning("unexpected task index payload: %s", index_path)
            return {}

        tasks: dict[str, TaskRecord] = {}
        changed = False
        for item in raw_data:
            if not isinstance(item, dict):
                continue
            try:
                task = TaskRecord.model_validate(item)
            except Exception:  # noqa: BLE001
                logger.exception("failed to parse task record from index")
                continue
            migrated_task = self._migrate_task_result_links(task)
            if migrated_task is not task:
                changed = True
            tasks[migrated_task.task_id] = migrated_task

        if changed:
            self._persist_loaded_tasks(tasks)
        return tasks

    def _persist_loaded_tasks(self, tasks: dict[str, TaskRecord]) -> None:
        index_path = settings.task_index_path
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                self._build_persist_payload(tasks),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("failed to persist migrated task index: %s", index_path)

    def _persist_tasks_unlocked(self) -> None:
        index_path = settings.task_index_path
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(
                self._build_persist_payload(self._tasks),
                encoding="utf-8",
            )
        except OSError:
            logger.exception("failed to persist task index: %s", index_path)

    def _build_persist_payload(self, tasks: dict[str, TaskRecord]) -> str:
        payload = [task.model_dump(mode="json") for task in tasks.values()]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def _mark_persist_dirty_unlocked(self) -> None:
        self._persist_dirty = True
        if self._persist_task is None or self._persist_task.done():
            self._persist_task = asyncio.create_task(self._persist_loop())

    async def _persist_loop(self) -> None:
        try:
            await asyncio.sleep(TASK_INDEX_PERSIST_DEBOUNCE_SECONDS)
            async with self._lock:
                if self._persist_dirty:
                    self._persist_dirty = False
                    self._persist_tasks_unlocked()
                self._persist_task = None
        except asyncio.CancelledError:
            return

    async def _flush_pending_persist(self) -> None:
        async with self._lock:
            if not self._persist_dirty:
                return
            self._persist_dirty = False
            self._persist_tasks_unlocked()

    def _should_persist_update_immediately(
        self,
        *,
        previous_task: TaskRecord,
        updated_task: TaskRecord,
        status_value: TaskStatus,
        result: TaskResult | None,
        error_message: str | None,
    ) -> bool:
        if status_value in TERMINAL_TASK_STATUSES:
            return True
        if result is not None or error_message is not None:
            return True
        if previous_task.status != updated_task.status and status_value == TaskStatus.PARSING:
            return True
        return False

    def _build_file_id_index(self, tasks: dict[str, TaskRecord]) -> dict[str, str]:
        index: dict[str, str] = {}
        for task_id, task in tasks.items():
            file_id = task.result.file_id if task.result is not None else None
            if isinstance(file_id, str) and file_id:
                index[file_id] = task_id
        return index

    def _update_file_id_index(self, previous_task: TaskRecord, current_task: TaskRecord) -> None:
        previous_file_id = previous_task.result.file_id if previous_task.result is not None else None
        current_file_id = current_task.result.file_id if current_task.result is not None else None

        if isinstance(previous_file_id, str) and previous_file_id:
            mapped_task_id = self._file_id_index.get(previous_file_id)
            if mapped_task_id == previous_task.task_id and previous_file_id != current_file_id:
                self._file_id_index.pop(previous_file_id, None)

        if isinstance(current_file_id, str) and current_file_id:
            self._file_id_index[current_file_id] = current_task.task_id

    def _migrate_task_result_links(self, task: TaskRecord) -> TaskRecord:
        result = task.result
        if result is None:
            return task

        if result.result_type == ResultType.DIRECT:
            if task.platform == Platform.IWARA and result.proxy_url:
                migrated_result = self._migrate_iwara_short_file_result(task=task, result=result)
                if migrated_result is not result:
                    return task.model_copy(update={"result": migrated_result})
                return task

            if not result.redirect_url:
                return task
            if result.play_url == result.redirect_url and result.download_url == result.redirect_url:
                return task

            migrated_result = result.model_copy(
                update={
                    "play_url": result.redirect_url,
                    "download_url": result.redirect_url,
                }
            )
            return task.model_copy(update={"result": migrated_result})

        if result.result_type == ResultType.DOWNLOAD and result.file_id and result.file_name:
            expected_play_url = storage_service.build_stream_url(result.file_id, result.file_name)
            if result.play_url == expected_play_url:
                return task

            migrated_result = result.model_copy(update={"play_url": expected_play_url})
            return task.model_copy(update={"result": migrated_result})

        return task

    def detect_platform(self, source_url: str) -> Platform:
        for platform, pattern in PLATFORM_PATTERNS:
            if pattern.search(source_url):
                return platform

        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前只支持 bilibili、twitter/x、youtube、reddit、iwara 链接，或纯 BV 号。",
        )


task_service = TaskService()
