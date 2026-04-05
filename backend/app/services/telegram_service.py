import asyncio
import json
import logging
import mimetypes
import re
import secrets
import string
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.schemas.parse import ParseRequest
from app.schemas.task import DeliveryMode, TaskRecord, TaskStatus
from app.services.storage_service import StoredFile, storage_service
from app.services.task_service import task_service
from app.utils.local_file_response import build_local_file_response
from app.utils.path import build_public_file_name

logger = logging.getLogger(__name__)

PASS_RESPONSE_HEADERS = {
    "accept-ranges",
    "cache-control",
    "content-disposition",
    "content-length",
    "content-range",
    "content-type",
    "etag",
    "expires",
    "last-modified",
}

TELEGRAM_DOWNLOAD_CHUNK_SIZE = 1024 * 1024
TELEGRAM_PROGRESS_UPDATE_INTERVAL_SECONDS = 1.5
TELEGRAM_PROGRESS_UPDATE_MIN_STEP = 3
TELEGRAM_BACKGROUND_PREPARE_MAX_ATTEMPTS = 5
TELEGRAM_BACKGROUND_PREPARE_RETRY_SECONDS = 15
TELEGRAM_TASK_POLL_INTERVAL_SECONDS = 1.5
TELEGRAM_TASK_POLL_TIMEOUT_SECONDS = 900

URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)

VIDEO_DOCUMENT_EXTENSIONS = {
    ".3gp",
    ".avi",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".webm",
}


class TelegramServiceError(RuntimeError):
    pass


@dataclass
class TelegramIncomingMedia:
    telegram_file_id: str
    telegram_file_unique_id: str
    file_name: str
    content_type: str
    file_size: int | None


@dataclass
class TelegramPublicFile:
    public_id: str
    telegram_file_id: str
    telegram_file_unique_id: str
    file_path: str | None
    cached_output_file_id: str | None
    file_name: str
    content_type: str
    file_size: int | None
    source_chat_id: int
    source_message_id: int
    created_at: datetime
    updated_at: datetime
    last_accessed_at: datetime


@dataclass(frozen=True)
class TelegramResolvedTarget:
    file_name: str
    content_type: str
    local_path: Path | None = None
    remote_url: str | None = None


@dataclass
class TelegramProgressMessage:
    chat_id: int
    message_id: int
    reply_to_message_id: int | None
    last_percent: int = -1
    last_text: str | None = None
    last_sent_at: float = 0.0


class TelegramService:
    def __init__(self) -> None:
        self._poll_client: httpx.AsyncClient | None = None
        self._command_client: httpx.AsyncClient | None = None
        self._file_client: httpx.AsyncClient | None = None
        self._stream_client: httpx.AsyncClient | None = None
        self._polling_task: asyncio.Task[None] | None = None
        self._update_tasks: set[asyncio.Task[None]] = set()
        self._prefetch_tasks: set[asyncio.Task[None]] = set()
        self._background_prepare_tasks: dict[str, asyncio.Task[None]] = {}
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._files = self._load_index()
        self._access_refresh_after: dict[str, float] = {}
        self._unique_index = self._build_unique_index(self._files)
        self._update_offset = self._load_state()
        self._last_error: str | None = None
        self._poll_error_streak = 0

    async def start(self) -> None:
        if self._poll_client is None:
            self._poll_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_poll_timeout_seconds + 5),
            )
        if self._command_client is None:
            self._command_client = self._build_http_client(read_timeout=settings.proxy_timeout_seconds)
        if self._file_client is None:
            self._file_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_file_timeout_seconds),
            )
        if self._stream_client is None:
            self._stream_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_file_timeout_seconds),
            )

        if not settings.telegram_bot_configured:
            return

        if settings.telegram_update_mode == "polling" and not settings.telegram_polling_enabled:
            return

        if self._polling_task is not None and not self._polling_task.done():
            return

        self._stop_event = asyncio.Event()
        if settings.telegram_update_mode == "webhook":
            await self._configure_webhook()
            return

        await self._delete_webhook()
        self._polling_task = asyncio.create_task(self._poll_updates_loop())

    async def stop(self) -> None:
        if self._polling_task is not None:
            self._stop_event.set()
            self._polling_task.cancel()
            await asyncio.gather(self._polling_task, return_exceptions=True)
            self._polling_task = None

        if self._update_tasks:
            await asyncio.gather(*self._update_tasks, return_exceptions=True)
            self._update_tasks.clear()

        if self._prefetch_tasks:
            for task in self._prefetch_tasks:
                task.cancel()
            await asyncio.gather(*self._prefetch_tasks, return_exceptions=True)
            self._prefetch_tasks.clear()

        if self._background_prepare_tasks:
            for task in self._background_prepare_tasks.values():
                task.cancel()
            await asyncio.gather(*self._background_prepare_tasks.values(), return_exceptions=True)
            self._background_prepare_tasks.clear()

        if self._poll_client is not None:
            await self._poll_client.aclose()
            self._poll_client = None
        if self._command_client is not None:
            await self._command_client.aclose()
            self._command_client = None
        if self._file_client is not None:
            await self._file_client.aclose()
            self._file_client = None
        if self._stream_client is not None:
            await self._stream_client.aclose()
            self._stream_client = None

    def status(self) -> dict[str, object]:
        return {
            "configured": settings.telegram_bot_configured,
            "update_mode": settings.telegram_update_mode,
            "polling_enabled": settings.telegram_polling_enabled,
            "polling_running": self._polling_task is not None and not self._polling_task.done(),
            "prefetch_enabled": settings.telegram_file_prefetch_enabled,
            "webhook_url": settings.telegram_webhook_target_url if settings.telegram_bot_configured else None,
            "webhook_secret_configured": settings.telegram_webhook_secret_value is not None,
            "allowed_chat_ids_configured": bool(settings.telegram_allowed_chat_id_set),
            "registered_files": len(self._files),
            "bot_api_base": settings.telegram_bot_api_base,
            "last_error": self._last_error,
        }

    async def build_public_file_response(
        self,
        file_id: str,
        request: Request,
        *,
        as_attachment: bool,
    ) -> Response | None:
        public_file = await self.get_public_file(file_id)
        if public_file is None:
            return None

        cached_output = await self._get_cached_output_file(public_file)
        if cached_output is not None:
            return build_local_file_response(
                path=cached_output.path,
                media_type=cached_output.content_type,
                file_name=cached_output.file_name,
                as_attachment=as_attachment,
            )

        resolved = await self._resolve_target(public_file=public_file, force_refresh=False)
        if resolved.local_path is not None:
            return build_local_file_response(
                path=resolved.local_path,
                media_type=resolved.content_type,
                file_name=resolved.file_name,
                as_attachment=as_attachment,
            )

        if resolved.remote_url is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Telegram 文件路径不可用，无法生成视频直链。",
            )

        client = self._require_stream_client()
        upstream_response = await self._open_remote_stream(
            client=client,
            remote_url=resolved.remote_url,
            request=request,
        )
        if upstream_response.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        }:
            await upstream_response.aclose()
            refreshed_target = await self._resolve_target(public_file=public_file, force_refresh=True)
            if refreshed_target.remote_url is None:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail="Telegram 文件已失效，且刷新后仍无法恢复。",
                )
            upstream_response = await self._open_remote_stream(
                client=client,
                remote_url=refreshed_target.remote_url,
                request=request,
            )
            resolved = refreshed_target

        filtered_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() in PASS_RESPONSE_HEADERS
        }
        self._apply_content_disposition(
            headers=filtered_headers,
            file_name=resolved.file_name,
            as_attachment=as_attachment,
        )
        filtered_headers.setdefault("Content-Type", resolved.content_type)

        if request.method == "HEAD":
            await upstream_response.aclose()
            return Response(
                content=b"",
                status_code=upstream_response.status_code,
                headers=filtered_headers,
            )

        return StreamingResponse(
            self._iter_stream(upstream_response),
            status_code=upstream_response.status_code,
            headers=filtered_headers,
            background=BackgroundTask(upstream_response.aclose),
        )

    async def get_public_file(self, file_id: str) -> TelegramPublicFile | None:
        public_file = self._files.get(file_id)
        if public_file is None:
            return None

        if not self._should_refresh_public_file_access(public_file.public_id):
            return public_file

        async with self._lock:
            current_file = self._files.get(file_id)
            if current_file is None:
                return None
            if not self._should_refresh_public_file_access(current_file.public_id):
                return current_file

            current_file.last_accessed_at = datetime.now(timezone.utc)
            self._mark_public_file_access_refreshed(current_file.public_id)
            self._persist_index_unlocked()
            return current_file

    async def prune_expired_entries(self, threshold: datetime) -> int:
        async with self._lock:
            expired_ids = [
                public_id
                for public_id, public_file in self._files.items()
                if public_file.last_accessed_at < threshold
            ]
            if not expired_ids:
                return 0

            for public_id in expired_ids:
                public_file = self._files.pop(public_id, None)
                if public_file is None:
                    continue
                self._unique_index.pop(public_file.telegram_file_unique_id, None)
                self._access_refresh_after.pop(public_id, None)

            self._persist_index_unlocked()
            return len(expired_ids)

    async def get_link_by_message(self, message: dict[str, Any]) -> tuple[str, str] | None:
        media = self._extract_supported_media(message)
        if media is None:
            return None

        chat_id = self._extract_chat_id(message)
        message_id = self._extract_message_id(message)
        if chat_id is None or message_id is None:
            raise TelegramServiceError("Telegram 消息缺少 chat_id 或 message_id。")

        public_file = await self._register_media(
            media=media,
            chat_id=chat_id,
            message_id=message_id,
        )
        link = storage_service.build_stream_url(public_file.public_id, public_file.file_name)
        return link, public_file.public_id

    async def _prepare_public_file_link(
        self,
        *,
        public_file: TelegramPublicFile,
        progress_message: TelegramProgressMessage | None,
    ) -> str:
        cached_output = await self._get_cached_output_file(public_file)
        if cached_output is not None:
            return storage_service.build_stream_url(public_file.public_id, public_file.file_name)

        resolved = await self._resolve_download_source(public_file=public_file, force_refresh=False)
        if resolved.local_path is not None:
            return storage_service.build_stream_url(public_file.public_id, public_file.file_name)
        if resolved.remote_url is None:
            raise TelegramServiceError("Telegram 文件当前无法获取可下载地址。")

        await self._download_public_file_to_output(
            public_file=public_file,
            remote_url=resolved.remote_url,
            progress_message=progress_message,
        )
        return storage_service.build_stream_url(public_file.public_id, public_file.file_name)

    async def _handle_media_update(
        self,
        *,
        message: dict[str, Any],
        chat_id: int,
        media: TelegramIncomingMedia,
    ) -> None:
        reply_to_message_id = self._extract_message_id(message)
        if reply_to_message_id is None:
            raise TelegramServiceError("Telegram message is missing message_id.")

        progress_message = await self._create_progress_message(
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            text="正在检查视频文件...",
        )
        public_file = await self._register_media(
            media=media,
            chat_id=chat_id,
            message_id=reply_to_message_id,
        )

        cached_output = await self._get_cached_output_file(public_file)
        if cached_output is not None:
            completion_text = self._build_completion_message(
                link=storage_service.build_stream_url(public_file.public_id, public_file.file_name),
                cached_locally=True,
            )
            if progress_message is not None:
                await self._safe_edit_message(
                    chat_id=progress_message.chat_id,
                    message_id=progress_message.message_id,
                    text=completion_text,
                )
            else:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text=completion_text,
                    reply_to_message_id=reply_to_message_id,
                )
            return

        if self._should_process_media_in_background(public_file):
            await self._update_progress_message(
                progress_message=progress_message,
                text=self._build_background_prepare_text(public_file=public_file),
                force=True,
            )
            self._schedule_background_prepare(
                public_id=public_file.public_id,
                progress_message=progress_message,
            )
            return

        try:
            link = await self._prepare_public_file_link(
                public_file=public_file,
                progress_message=progress_message,
            )
        except Exception:
            if progress_message is not None:
                await self._safe_edit_message(
                    chat_id=progress_message.chat_id,
                    message_id=progress_message.message_id,
                    text="处理失败，请稍后重试。",
                )
            raise

        completion_text = self._build_completion_message(link=link, cached_locally=True)
        if progress_message is not None:
            await self._safe_edit_message(
                chat_id=progress_message.chat_id,
                message_id=progress_message.message_id,
                text=completion_text,
            )
        else:
            await self._safe_send_message(
                chat_id=chat_id,
                text=completion_text,
                reply_to_message_id=reply_to_message_id,
            )

        if settings.telegram_file_prefetch_enabled:
            self._schedule_file_path_prefetch(public_id=public_file.public_id)

    def _should_process_media_in_background(self, public_file: TelegramPublicFile) -> bool:
        threshold_mb = max(0, settings.telegram_sync_cache_max_mb)
        threshold_bytes = threshold_mb * 1024 * 1024
        if threshold_bytes <= 0:
            return True

        file_size = public_file.file_size
        if not isinstance(file_size, int) or file_size <= 0:
            return True
        return file_size > threshold_bytes

    def _build_background_prepare_text(self, *, public_file: TelegramPublicFile) -> str:
        size_text = self._format_file_size_clean(public_file.file_size)
        if size_text == "未知":
            return "文件较大，已切换为后台处理。\n处理完成后会在这条消息里更新直链。"
        return (
            "文件较大，已切换为后台处理。\n"
            f"文件大小：{size_text}\n"
            "处理完成后会在这条消息里更新直链。"
        )

    def _build_completion_message(self, *, link: str, cached_locally: bool) -> str:
        if cached_locally:
            return f"视频直链已生成：\n{link}\n\n资源已就绪，可直接播放。"
        return f"视频直链已生成：\n{link}"

    async def _handle_url_message(
        self,
        *,
        message: dict[str, Any],
        chat_id: int,
        source_url: str,
    ) -> None:
        reply_to_message_id = self._extract_message_id(message)
        if reply_to_message_id is None:
            raise TelegramServiceError("Telegram message is missing message_id.")

        progress_message = await self._create_progress_message(
            chat_id=chat_id,
            reply_to_message_id=reply_to_message_id,
            text="正在创建解析任务...",
        )

        try:
            payload = ParseRequest(
                url=source_url,
                delivery_mode=DeliveryMode.AUTO,
            )
            task = await task_service.create_task(payload)
            asyncio.create_task(task_service.run_download_pipeline(task.task_id))
            link = await self._wait_for_task_link(task_id=task.task_id, progress_message=progress_message)
        except Exception:
            if progress_message is not None:
                await self._safe_edit_message(
                    chat_id=progress_message.chat_id,
                    message_id=progress_message.message_id,
                    text="链接解析失败，请稍后重试。",
                )
            raise

        completion_text = self._build_completion_message(link=link, cached_locally=False)
        if progress_message is not None:
            await self._safe_edit_message(
                chat_id=progress_message.chat_id,
                message_id=progress_message.message_id,
                text=completion_text,
            )
        else:
            await self._safe_send_message(
                chat_id=chat_id,
                text=completion_text,
                reply_to_message_id=reply_to_message_id,
            )

    async def _wait_for_task_link(
        self,
        *,
        task_id: str,
        progress_message: TelegramProgressMessage | None,
    ) -> str:
        deadline = monotonic() + TELEGRAM_TASK_POLL_TIMEOUT_SECONDS
        while monotonic() < deadline:
            task = await task_service.get_task(task_id)
            if task is None:
                raise TelegramServiceError("解析任务不存在。")

            if task.status == TaskStatus.SUCCESS:
                link = self._pick_task_link(task)
                if link is None:
                    raise TelegramServiceError("任务已完成，但没有拿到可用链接。")
                return link

            if task.status == TaskStatus.FAILED:
                raise TelegramServiceError(task.error_message or task.message or "解析任务失败。")

            await self._update_progress_message(
                progress_message=progress_message,
                text=self._build_task_progress_text(task),
            )
            await asyncio.sleep(TELEGRAM_TASK_POLL_INTERVAL_SECONDS)

        raise TelegramServiceError("解析任务超时，请稍后重试。")

    def _pick_task_link(self, task: TaskRecord) -> str | None:
        result = task.result
        if result is None:
            return None

        for candidate in (
            result.play_url,
            result.download_url,
            result.proxy_url,
            result.redirect_url,
            result.direct_url,
            result.video_proxy_url,
            result.video_redirect_url,
        ):
            if isinstance(candidate, str) and candidate:
                return candidate
        return None

    def _build_task_progress_text(self, task: TaskRecord) -> str:
        title = task.title.strip() if isinstance(task.title, str) and task.title.strip() else "当前链接"
        title_line = title if len(title) <= 40 else f"{title[:37]}..."
        status_text_map = {
            TaskStatus.PENDING: "任务已创建，等待开始。",
            TaskStatus.PARSING: "正在解析视频信息...",
            TaskStatus.DOWNLOADING: "正在下载媒体资源...",
            TaskStatus.MERGING: "正在处理音视频流...",
            TaskStatus.UPLOADING: "正在生成本站短链...",
        }
        status_text = status_text_map.get(task.status, "正在处理中...")
        return f"{status_text}\n{title_line}\n进度：{task.progress}%"

    def _schedule_background_prepare(
        self,
        *,
        public_id: str,
        progress_message: TelegramProgressMessage | None,
    ) -> None:
        existing_task = self._background_prepare_tasks.get(public_id)
        if existing_task is not None and not existing_task.done():
            return

        task = asyncio.create_task(
            self._prepare_public_file_in_background(
                public_id=public_id,
                progress_message=progress_message,
            )
        )
        self._background_prepare_tasks[public_id] = task
        task.add_done_callback(lambda _task, key=public_id: self._background_prepare_tasks.pop(key, None))

    async def _prepare_public_file_in_background(
        self,
        *,
        public_id: str,
        progress_message: TelegramProgressMessage | None,
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(1, TELEGRAM_BACKGROUND_PREPARE_MAX_ATTEMPTS + 1):
            public_file = await self.get_public_file(public_id)
            if public_file is None:
                return

            try:
                link = await self._prepare_public_file_link(
                    public_file=public_file,
                    progress_message=progress_message,
                )
            except Exception as exc:  # noqa: BLE001
                last_error = exc if isinstance(exc, Exception) else RuntimeError(str(exc))
                logger.warning(
                    "telegram background prepare failed: public_id=%s attempt=%s/%s error=%s",
                    public_id,
                    attempt,
                    TELEGRAM_BACKGROUND_PREPARE_MAX_ATTEMPTS,
                    exc,
                )
                if attempt >= TELEGRAM_BACKGROUND_PREPARE_MAX_ATTEMPTS:
                    break
                await self._update_progress_message(
                    progress_message=progress_message,
                    text=(
                        "文件仍在准备中，请稍后再试。\n"
                        f"第 {attempt}/{TELEGRAM_BACKGROUND_PREPARE_MAX_ATTEMPTS} 次尝试未完成。"
                    ),
                    force=True,
                )
                await asyncio.sleep(TELEGRAM_BACKGROUND_PREPARE_RETRY_SECONDS)
                continue

            await self._update_progress_message(
                progress_message=progress_message,
                text=self._build_completion_message(link=link, cached_locally=True),
                force=True,
            )
            return

        if progress_message is not None:
            error_text = "当前 Telegram 文件获取超时，请稍后重试。"
            if last_error is not None and not self._is_timeout_error(last_error):
                error_text = "处理失败，请稍后重试。"
            await self._safe_edit_message(
                chat_id=progress_message.chat_id,
                message_id=progress_message.message_id,
                text=error_text,
            )

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = self._extract_update_message(update)
        if message is None:
            return

        self._log_update_summary(update=update, message=message)

        chat_id = self._extract_chat_id(message)
        if chat_id is None:
            return

        if not self._is_chat_allowed(chat_id):
            await self._safe_send_message(
                chat_id=chat_id,
                text="当前 bot 未开放给这个 chat 使用。",
                reply_to_message_id=self._extract_message_id(message),
            )
            return

        text = message.get("text")
        if isinstance(text, str) and text.strip().lower() in {"/start", "/help"}:
            await self._safe_send_message(
                chat_id=chat_id,
                text="把 Telegram 视频或 video/* 文件直接发送给我，我会返回本站短链。",
                reply_to_message_id=self._extract_message_id(message),
            )
            return

        media = self._extract_supported_media(message)
        if media is not None:
            await self._handle_media_update(
                message=message,
                chat_id=chat_id,
                media=media,
            )
            return

        supported_url = self._extract_supported_url(message)
        if supported_url is not None:
            await self._handle_url_message(
                message=message,
                chat_id=chat_id,
                source_url=supported_url,
            )
            return

        media = self._extract_supported_media(message)
        if media is not None:
            reply_to_message_id = self._extract_message_id(message)
            if reply_to_message_id is None:
                raise TelegramServiceError("Telegram 消息缺少 message_id。")

            progress_message = await self._create_progress_message(
                chat_id=chat_id,
                reply_to_message_id=reply_to_message_id,
                text="正在检查 Telegram 文件...",
            )
            try:
                public_file = await self._register_media(
                    media=media,
                    chat_id=chat_id,
                    message_id=reply_to_message_id,
                )
                link = await self._prepare_public_file_link(
                    public_file=public_file,
                    progress_message=progress_message,
                )
            except Exception:
                if progress_message is not None:
                    await self._safe_edit_message(
                        chat_id=progress_message.chat_id,
                        message_id=progress_message.message_id,
                        text="处理失败，请稍后重试。",
                    )
                raise

            if progress_message is not None:
                await self._safe_edit_message(
                    chat_id=progress_message.chat_id,
                    message_id=progress_message.message_id,
                    text=f"已生成短链：\n{link}",
                )
            else:
                await self._safe_send_message(
                    chat_id=chat_id,
                    text=link,
                    reply_to_message_id=reply_to_message_id,
                )

            if settings.telegram_file_prefetch_enabled:
                self._schedule_file_path_prefetch(public_id=public_file.public_id)
            return

        link_result = await self.get_link_by_message(message)
        if link_result is None:
            if self._contains_media_payload(message):
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="只支持视频消息或 mime 为 video/* 的文件。",
                    reply_to_message_id=self._extract_message_id(message),
                )
                return

            if self._is_forwarded_message(message):
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="这条转发消息里没有可直接解析的视频文件。常见原因是原消息来自其他 Bot，或该频道消息未把媒体文件一并转发出来。请直接发送视频文件，或改发能直接打开的视频链接。",
                    reply_to_message_id=self._extract_message_id(message),
                )
            return

        link, public_id = link_result
        await self._safe_send_message(
            chat_id=chat_id,
            text=link,
            reply_to_message_id=self._extract_message_id(message),
        )

        if settings.telegram_file_prefetch_enabled:
            self._schedule_file_path_prefetch(public_id=public_id)

    async def handle_webhook_update(
        self,
        update: dict[str, Any],
        *,
        secret_token: str | None,
    ) -> None:
        if settings.telegram_update_mode != "webhook":
            raise TelegramServiceError("Telegram webhook mode is not enabled.")
        if not self._is_valid_webhook_secret(secret_token):
            raise TelegramServiceError("Telegram webhook secret is invalid.")

        update_id = update.get("update_id")
        self._schedule_update_handling(update=update, update_id=update_id)

    def _schedule_update_handling(self, *, update: dict[str, Any], update_id: object) -> None:
        task = asyncio.create_task(self._handle_update_safe(update=update, update_id=update_id))
        self._update_tasks.add(task)
        task.add_done_callback(self._update_tasks.discard)

    async def _poll_updates_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                updates = await self._get_updates(offset=self._update_offset)
            except Exception as exc:  # noqa: BLE001
                self._record_error(f"Telegram polling failed: {exc}")
                self._log_polling_failure(exc)
                await self._sleep_until_next_poll()
                continue

            self._poll_error_streak = 0

            if not updates:
                await self._sleep_until_next_poll()
                continue

            for update in updates:
                update_id = update.get("update_id")
                if isinstance(update_id, int):
                    self._update_offset = max(self._update_offset, update_id + 1)
                    self._persist_state()
                self._schedule_update_handling(update=update, update_id=update_id)

    async def _sleep_until_next_poll(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=max(0, settings.telegram_poll_interval_seconds),
            )
        except asyncio.CancelledError:
            return
        except TimeoutError:
            return

    async def _get_updates(self, offset: int) -> list[dict[str, Any]]:
        result = await self._call_api(
            method="getUpdates",
            payload={
                "offset": offset,
                "timeout": settings.telegram_poll_timeout_seconds,
                "allowed_updates": [
                    "message",
                    "edited_message",
                    "channel_post",
                    "edited_channel_post",
                ],
            },
            client_kind="poll",
        )
        if not isinstance(result, list):
            raise TelegramServiceError("Telegram getUpdates 返回格式不正确。")

        updates: list[dict[str, Any]] = []
        for item in result:
            if isinstance(item, dict):
                updates.append(item)
        return updates

    async def _delete_webhook(self) -> None:
        try:
            await self._call_api(
                method="deleteWebhook",
                payload={"drop_pending_updates": False},
                client_kind="command",
            )
            self._clear_error()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"Failed to delete telegram webhook before polling: {exc}")
            logger.warning(
                "Telegram Bot API is unreachable at %s; webhook reset skipped. Check TELEGRAM_BOT_API_BASE or start telegram-bot-api.",
                settings.telegram_bot_api_base,
            )

    async def _configure_webhook(self) -> None:
        payload: dict[str, Any] = {
            "url": settings.telegram_webhook_target_url,
            "drop_pending_updates": False,
            "allowed_updates": [
                "message",
                "edited_message",
                "channel_post",
                "edited_channel_post",
            ],
        }
        secret_token = settings.telegram_webhook_secret_value
        if isinstance(secret_token, str) and secret_token:
            payload["secret_token"] = secret_token

        await self._call_api(
            method="setWebhook",
            payload=payload,
            client_kind="command",
        )
        self._clear_error()

    def _is_valid_webhook_secret(self, secret_token: str | None) -> bool:
        expected = settings.telegram_webhook_secret_value
        if not isinstance(expected, str) or not expected:
            return True
        return isinstance(secret_token, str) and secret_token == expected

    async def _handle_update_safe(self, update: dict[str, Any], update_id: object) -> None:
        try:
            await self.handle_update(update)
        except Exception as exc:  # noqa: BLE001
            logger.exception("failed to handle telegram update: %s", update_id)
            await self._notify_update_failure(update=update, exc=exc)

    async def _notify_update_failure(self, update: dict[str, Any], exc: Exception) -> None:
        message = self._extract_update_message(update)
        if message is None:
            return

        chat_id = self._extract_chat_id(message)
        if chat_id is None or not self._is_chat_allowed(chat_id):
            return

        error_text = "当前 Telegram 文件处理失败，请稍后重试。"
        if self._is_timeout_error(exc):
            error_text = "当前 Telegram 文件获取超时，请稍后重试，或先发送较小的视频测试。"

        await self._safe_send_message(
            chat_id=chat_id,
            text=error_text,
            reply_to_message_id=self._extract_message_id(message),
        )

    async def _register_media(
        self,
        *,
        media: TelegramIncomingMedia,
        chat_id: int,
        message_id: int,
    ) -> TelegramPublicFile:
        now = datetime.now(timezone.utc)

        async with self._lock:
            public_id = self._unique_index.get(media.telegram_file_unique_id)
            if public_id is None:
                public_id = self._generate_public_id()
                while public_id in self._files:
                    public_id = self._generate_public_id()

            public_file = self._files.get(public_id)
            if public_file is None:
                public_file = TelegramPublicFile(
                    public_id=public_id,
                    telegram_file_id=media.telegram_file_id,
                    telegram_file_unique_id=media.telegram_file_unique_id,
                    # 这里先不阻塞等待 getFile。
                    # 对大视频来说，本地 Bot API 可能要很久才能返回 file_path。
                    # 先生成短链并回复给用户，再在后台预热 file_path，首次访问时也会兜底刷新。
                    file_path=None,
                    cached_output_file_id=None,
                    file_name=media.file_name,
                    content_type=media.content_type,
                    file_size=media.file_size,
                    source_chat_id=chat_id,
                    source_message_id=message_id,
                    created_at=now,
                    updated_at=now,
                    last_accessed_at=now,
                )
                self._files[public_id] = public_file
            else:
                public_file.telegram_file_id = media.telegram_file_id
                public_file.telegram_file_unique_id = media.telegram_file_unique_id
                # 相同资源再次出现时保留已解析出的 file_path，避免重复触发 getFile。
                public_file.file_name = media.file_name
                public_file.content_type = media.content_type
                public_file.file_size = media.file_size
                public_file.source_chat_id = chat_id
                public_file.source_message_id = message_id
                public_file.updated_at = now
                public_file.last_accessed_at = now

            self._unique_index[media.telegram_file_unique_id] = public_id
            self._persist_index_unlocked()
            return public_file

    def _schedule_file_path_prefetch(self, *, public_id: str) -> None:
        task = asyncio.create_task(self._prefetch_file_path(public_id=public_id))
        self._prefetch_tasks.add(task)
        task.add_done_callback(self._prefetch_tasks.discard)

    async def _prefetch_file_path(self, *, public_id: str) -> None:
        public_file = await self.get_public_file(public_id)
        if public_file is None or public_file.file_path:
            return

        try:
            await self._refresh_file_path(public_file)
        except Exception as exc:  # noqa: BLE001
            logger.warning("telegram file prefetch failed: public_id=%s error=%s", public_id, exc)

    async def _get_cached_output_file(self, public_file: TelegramPublicFile) -> StoredFile | None:
        cached_output_file_id = public_file.cached_output_file_id
        if not isinstance(cached_output_file_id, str) or not cached_output_file_id:
            return None

        stored_file = await storage_service.get_file(cached_output_file_id)
        if stored_file is not None:
            return stored_file

        async with self._lock:
            current = self._files.get(public_file.public_id)
            if current is None or current.cached_output_file_id != cached_output_file_id:
                return None
            current.cached_output_file_id = None
            current.updated_at = datetime.now(timezone.utc)
            self._persist_index_unlocked()
        return None

    async def _set_cached_output_file_id(self, public_id: str, cached_output_file_id: str) -> None:
        async with self._lock:
            current = self._files.get(public_id)
            if current is None:
                return
            current.cached_output_file_id = cached_output_file_id
            current.updated_at = datetime.now(timezone.utc)
            self._persist_index_unlocked()

    async def _resolve_download_source(
        self,
        *,
        public_file: TelegramPublicFile,
        force_refresh: bool,
    ) -> TelegramResolvedTarget:
        file_path = public_file.file_path
        if force_refresh or not file_path:
            file_path = await self._refresh_file_path(public_file)

        if isinstance(file_path, str) and file_path:
            local_path = self._resolve_accessible_local_path(file_path)
            if local_path is not None:
                return TelegramResolvedTarget(
                    file_name=public_file.file_name,
                    content_type=public_file.content_type,
                    local_path=local_path,
                )
            return TelegramResolvedTarget(
                file_name=public_file.file_name,
                content_type=public_file.content_type,
                remote_url=self._build_file_download_url(file_path),
            )

        raise TelegramServiceError("Telegram 文件当前没有可用的 file_path。")

    async def _download_public_file_to_output(
        self,
        *,
        public_file: TelegramPublicFile,
        remote_url: str,
        progress_message: TelegramProgressMessage | None,
    ) -> None:
        cached_output = await self._get_cached_output_file(public_file)
        if cached_output is not None:
            return

        cache_dir = settings.output_dir / "telegram-cache" / public_file.public_id
        cache_dir.mkdir(parents=True, exist_ok=True)
        final_path = cache_dir / build_public_file_name(
            public_file.file_name,
            fallback_stem="telegram-video",
        )
        part_path = final_path.with_name(f"{final_path.name}.part")
        if part_path.exists():
            part_path.unlink(missing_ok=True)

        downloaded_bytes = 0
        total_bytes: int | None = None
        try:
            async with self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_file_timeout_seconds),
            ) as client:
                async with client.stream("GET", remote_url) as response:
                    response.raise_for_status()
                    total_bytes = self._parse_content_length(response.headers.get("content-length"))
                    await self._update_download_progress_message(
                        progress_message=progress_message,
                        file_name=public_file.file_name,
                        downloaded_bytes=downloaded_bytes,
                        total_bytes=total_bytes,
                        force=True,
                    )
                    with part_path.open("wb") as output_stream:
                        async for chunk in response.aiter_bytes(TELEGRAM_DOWNLOAD_CHUNK_SIZE):
                            if not chunk:
                                continue
                            output_stream.write(chunk)
                            downloaded_bytes += len(chunk)
                            await self._update_download_progress_message(
                                progress_message=progress_message,
                                file_name=public_file.file_name,
                                downloaded_bytes=downloaded_bytes,
                                total_bytes=total_bytes,
                            )
        except httpx.HTTPError as exc:
            part_path.unlink(missing_ok=True)
            raise TelegramServiceError(f"Telegram 文件下载失败：{exc}") from exc
        except OSError as exc:
            part_path.unlink(missing_ok=True)
            raise TelegramServiceError(f"Telegram 文件写入失败：{exc}") from exc

        part_path.replace(final_path)
        await self._update_progress_message(
            progress_message=progress_message,
            text="下载完成，正在生成本地短链...",
            force=True,
        )
        await self._update_progress_message(
            progress_message=progress_message,
            text="转存完成，正在生成站内短链...",
            force=True,
        )
        result = await storage_service.register_downloaded_file(final_path)
        await self._set_cached_output_file_id(public_id=public_file.public_id, cached_output_file_id=result.file_id)

    async def _resolve_target(
        self,
        *,
        public_file: TelegramPublicFile,
        force_refresh: bool,
    ) -> TelegramResolvedTarget:
        try:
            return await self._resolve_download_source(public_file=public_file, force_refresh=force_refresh)
        except TelegramServiceError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc

        file_path = public_file.file_path
        if force_refresh or not file_path:
            file_path = await self._refresh_file_path(public_file)

        if isinstance(file_path, str) and file_path:
            local_path = self._resolve_accessible_local_path(file_path)
            # 本地 Bot API 在 local 模式下可能直接返回绝对路径。
            # 如果 telegram-bot-api 跑在 Docker 容器里，这里会先尝试把容器路径映射到宿主机路径。
            if local_path is not None:
                return TelegramResolvedTarget(
                    file_name=public_file.file_name,
                    content_type=public_file.content_type,
                    local_path=local_path,
                )

            raw_local_path = Path(file_path)
            if not raw_local_path.is_absolute():
                return TelegramResolvedTarget(
                    file_name=public_file.file_name,
                    content_type=public_file.content_type,
                    remote_url=self._build_file_download_url(file_path),
                )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram 本地文件路径不可访问，请确认 Bot API 与当前服务部署在同一台机器。",
        )

    def _resolve_accessible_local_path(self, file_path: str) -> Path | None:
        local_path = Path(file_path)
        if local_path.is_absolute() and local_path.exists():
            return local_path

        mapped_path = self._map_local_file_path(local_path)
        if mapped_path is not None and mapped_path.exists():
            return mapped_path
        return None

    def _map_local_file_path(self, local_path: Path) -> Path | None:
        if not local_path.is_absolute():
            return None

        source_prefix = settings.telegram_local_file_source_prefix
        target_prefix = settings.telegram_local_file_target_prefix
        if not source_prefix or not target_prefix:
            return None

        normalized_path = local_path.as_posix()
        if normalized_path != source_prefix and not normalized_path.startswith(f"{source_prefix}/"):
            return None

        relative_path = normalized_path[len(source_prefix):].lstrip("/")
        target_path = Path(target_prefix)
        if not relative_path:
            return target_path
        return target_path / relative_path

    async def _refresh_file_path(self, public_file: TelegramPublicFile) -> str | None:
        file_path = await self._get_file_path(public_file.telegram_file_id)
        async with self._lock:
            current = self._files.get(public_file.public_id)
            if current is None:
                return file_path
            current.file_path = file_path
            current.updated_at = datetime.now(timezone.utc)
            self._persist_index_unlocked()
            return current.file_path

    async def _get_file_path(self, telegram_file_id: str) -> str | None:
        result = await self._call_api(
            method="getFile",
            payload={"file_id": telegram_file_id},
            timeout_seconds=settings.telegram_file_timeout_seconds,
            client_kind="file",
        )
        if not isinstance(result, dict):
            raise TelegramServiceError("Telegram getFile 返回格式不正确。")

        file_path = result.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
        raise TelegramServiceError("Telegram getFile 没有返回 file_path。")

    async def _call_api(
        self,
        method: str,
        payload: dict[str, Any],
        timeout_seconds: float | None = None,
        client_kind: str = "command",
    ) -> Any:
        try:
            response = await self._post_api_with_retry(
                method=method,
                payload=payload,
                timeout_seconds=timeout_seconds,
                client_kind=client_kind,
            )
        except httpx.HTTPError as exc:
            error_detail = str(exc).strip() or exc.__class__.__name__
            raise TelegramServiceError(f"Telegram API 请求失败：{error_detail}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramServiceError("Telegram API ???????? JSON?") from exc

        if not isinstance(data, dict):
            raise TelegramServiceError("Telegram API ????????")
        if data.get("ok") is not True:
            description = data.get("description") or "unknown telegram api error"
            raise TelegramServiceError(f"Telegram API ?????{description}")
        self._clear_error()
        return data.get("result")

    async def _post_api_with_retry(
        self,
        *,
        method: str,
        payload: dict[str, Any],
        timeout_seconds: float | None,
        client_kind: str,
    ) -> httpx.Response:
        url = self._build_api_url(method)
        last_exc: httpx.HTTPError | None = None
        for attempt in range(2):
            client = self._get_api_client(client_kind)
            try:
                response = await client.post(url, json=payload, timeout=timeout_seconds)
                response.raise_for_status()
                return response
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == 0 and self._should_refresh_client(exc):
                    await self._reset_api_client(client_kind)
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    async def _open_remote_stream_with_retry(
        self,
        *,
        remote_url: str,
        request_method: str,
        headers: dict[str, str],
    ) -> httpx.Response:
        last_exc: httpx.HTTPError | None = None
        for attempt in range(2):
            client = self._require_stream_client()
            try:
                built_request = client.build_request(
                    method=request_method,
                    url=remote_url,
                    headers=headers,
                )
                return await client.send(built_request, stream=True)
            except httpx.HTTPError as exc:
                last_exc = exc
                if attempt == 0 and self._should_refresh_client(exc):
                    await self._reset_api_client("stream")
                    continue
                raise

        assert last_exc is not None
        raise last_exc

    def _should_refresh_client(self, exc: httpx.HTTPError) -> bool:
        return isinstance(
            exc,
            (
                httpx.ConnectError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.ReadTimeout,
                httpx.WriteTimeout,
                httpx.CloseError,
                httpx.RemoteProtocolError,
            ),
        )

    async def _reset_api_client(self, client_kind: str) -> None:
        if client_kind == "poll":
            if self._poll_client is not None:
                await self._poll_client.aclose()
            self._poll_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_poll_timeout_seconds + 5),
            )
            logger.warning("telegram poll client was reset after request failure")
            return

        if client_kind == "file":
            if self._file_client is not None:
                await self._file_client.aclose()
            self._file_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_file_timeout_seconds),
            )
            logger.warning("telegram file client was reset after request failure")
            return

        if client_kind == "stream":
            if self._stream_client is not None:
                await self._stream_client.aclose()
            self._stream_client = self._build_http_client(
                read_timeout=max(settings.proxy_timeout_seconds, settings.telegram_file_timeout_seconds),
            )
            logger.warning("telegram stream client was reset after request failure")
            return

        if self._command_client is not None:
            await self._command_client.aclose()
        self._command_client = self._build_http_client(read_timeout=settings.proxy_timeout_seconds)
        logger.warning("telegram command client was reset after request failure")

    def _record_error(self, message: str) -> None:
        self._last_error = message

    def _clear_error(self) -> None:
        self._last_error = None

    def _log_polling_failure(self, exc: Exception) -> None:
        self._poll_error_streak += 1
        if self._poll_error_streak == 1:
            logger.warning(
                "Telegram polling cannot reach %s. Start telegram-bot-api or fix TELEGRAM_BOT_API_BASE. error=%s",
                settings.telegram_bot_api_base,
                exc,
            )
            return
        if self._poll_error_streak % 30 == 0:
            logger.warning(
                "Telegram polling is still failing after %s attempts. error=%s",
                self._poll_error_streak,
                exc,
            )

    def _log_update_summary(self, *, update: dict[str, Any], message: dict[str, Any]) -> None:
        update_id = update.get("update_id")
        chat_id = self._extract_chat_id(message)
        message_id = self._extract_message_id(message)
        media_kinds = [
            key
            for key in ("video", "document", "animation", "video_note", "audio", "voice", "photo", "sticker")
            if key in message
        ]
        logger.info(
            "telegram update received: update_id=%s chat_id=%s message_id=%s forwarded=%s media=%s via_bot=%s sender_chat=%s",
            update_id,
            chat_id,
            message_id,
            self._is_forwarded_message(message),
            ",".join(media_kinds) if media_kinds else "-",
            isinstance(message.get("via_bot"), dict),
            isinstance(message.get("sender_chat"), dict),
        )

    def _is_timeout_error(self, exc: BaseException) -> bool:
        current: BaseException | None = exc
        visited: set[int] = set()
        while current is not None:
            object_id = id(current)
            if object_id in visited:
                break
            visited.add(object_id)

            if isinstance(current, (httpx.TimeoutException, TimeoutError)):
                return True
            if current.__class__.__name__ in {"ReadTimeout", "WriteTimeout", "PoolTimeout", "ConnectTimeout"}:
                return True

            next_error = current.__cause__ or current.__context__
            current = next_error if isinstance(next_error, BaseException) else None
        return False

    async def _safe_send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None,
    ) -> None:
        if not settings.telegram_bot_configured:
            return

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if isinstance(reply_to_message_id, int):
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True

        try:
            await self._call_api(method="sendMessage", payload=payload, client_kind="command")
        except Exception:  # noqa: BLE001
            logger.exception("failed to send telegram message to chat_id=%s", chat_id)

    async def _create_progress_message(
        self,
        *,
        chat_id: int,
        reply_to_message_id: int | None,
        text: str,
    ) -> TelegramProgressMessage | None:
        result = await self._send_message(
            chat_id=chat_id,
            text=text,
            reply_to_message_id=reply_to_message_id,
        )
        if not isinstance(result, dict):
            return None

        message_id = result.get("message_id")
        if not isinstance(message_id, int):
            return None
        return TelegramProgressMessage(
            chat_id=chat_id,
            message_id=message_id,
            reply_to_message_id=reply_to_message_id,
            last_text=text,
            last_sent_at=monotonic(),
        )

    async def _send_message(
        self,
        *,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None,
    ) -> dict[str, Any] | None:
        if not settings.telegram_bot_configured:
            return None

        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if isinstance(reply_to_message_id, int):
            payload["reply_to_message_id"] = reply_to_message_id
            payload["allow_sending_without_reply"] = True

        try:
            result = await self._call_api(method="sendMessage", payload=payload, client_kind="command")
        except Exception:  # noqa: BLE001
            logger.exception("failed to send telegram message to chat_id=%s", chat_id)
            return None
        return result if isinstance(result, dict) else None

    async def _safe_edit_message(
        self,
        *,
        chat_id: int,
        message_id: int,
        text: str,
    ) -> None:
        if not settings.telegram_bot_configured:
            return

        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        try:
            await self._call_api(method="editMessageText", payload=payload, client_kind="command")
        except Exception as exc:  # noqa: BLE001
            error_text = str(exc)
            if "message is not modified" in error_text:
                return
            logger.exception("failed to edit telegram message chat_id=%s message_id=%s", chat_id, message_id)

    async def _update_progress_message(
        self,
        *,
        progress_message: TelegramProgressMessage | None,
        text: str,
        force: bool = False,
    ) -> None:
        if progress_message is None:
            return
        if not force and progress_message.last_text == text:
            return

        progress_message.last_text = text
        progress_message.last_sent_at = monotonic()
        await self._safe_edit_message(
            chat_id=progress_message.chat_id,
            message_id=progress_message.message_id,
            text=text,
        )

    async def _update_download_progress_message(
        self,
        *,
        progress_message: TelegramProgressMessage | None,
        file_name: str,
        downloaded_bytes: int,
        total_bytes: int | None,
        force: bool = False,
    ) -> None:
        if progress_message is None:
            return

        percent = self._calculate_progress_percent(downloaded_bytes, total_bytes)
        now = monotonic()
        if not force:
            if percent >= 0 and progress_message.last_percent >= 0:
                if percent - progress_message.last_percent < TELEGRAM_PROGRESS_UPDATE_MIN_STEP:
                    if now - progress_message.last_sent_at < TELEGRAM_PROGRESS_UPDATE_INTERVAL_SECONDS:
                        return
            elif now - progress_message.last_sent_at < TELEGRAM_PROGRESS_UPDATE_INTERVAL_SECONDS:
                return

        progress_message.last_percent = percent
        await self._update_progress_message(
            progress_message=progress_message,
            text=self._build_download_progress_text_clean(
                file_name=file_name,
                downloaded_bytes=downloaded_bytes,
                total_bytes=total_bytes,
                percent=percent,
            ),
            force=True,
        )

    def _build_download_progress_text_clean(
        self,
        *,
        file_name: str,
        downloaded_bytes: int,
        total_bytes: int | None,
        percent: int,
    ) -> str:
        display_name = file_name if len(file_name) <= 48 else f"{file_name[:45]}..."
        if percent >= 0:
            bar = self._build_progress_bar_ascii(percent)
            return (
                "正在转存 Telegram 视频到本地缓存...\n"
                f"{display_name}\n"
                f"[{bar}] {percent}%\n"
                f"已下载 {self._format_file_size_clean(downloaded_bytes)} / {self._format_file_size_clean(total_bytes)}"
            )
        return (
            "正在转存 Telegram 视频到本地缓存...\n"
            f"{display_name}\n"
            f"已下载 {self._format_file_size_clean(downloaded_bytes)}"
        )

    def _build_progress_bar_ascii(self, percent: int) -> str:
        normalized_percent = max(0, min(100, percent))
        total_blocks = 12
        filled_blocks = min(total_blocks, normalized_percent * total_blocks // 100)
        return "#" * filled_blocks + "-" * (total_blocks - filled_blocks)

    def _format_file_size_clean(self, size: int | None) -> str:
        if not isinstance(size, int) or size < 0:
            return "未知"

        value = float(size)
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024

        return "未知"

    def _build_download_progress_text(
        self,
        *,
        file_name: str,
        downloaded_bytes: int,
        total_bytes: int | None,
        percent: int,
    ) -> str:
        display_name = file_name if len(file_name) <= 48 else f"{file_name[:45]}..."
        if percent >= 0:
            bar = self._build_progress_bar(percent)
            return (
                "正在转存 Telegram 视频到本地...\n"
                f"{display_name}\n"
                f"[{bar}] {percent}%\n"
                f"{self._format_file_size(downloaded_bytes)} / {self._format_file_size(total_bytes)}"
            )
        return (
            "正在转存 Telegram 视频到本地...\n"
            f"{display_name}\n"
            f"已下载 {self._format_file_size(downloaded_bytes)}"
        )

    def _build_progress_bar(self, percent: int) -> str:
        normalized_percent = max(0, min(100, percent))
        total_blocks = 12
        filled_blocks = min(total_blocks, normalized_percent * total_blocks // 100)
        return "█" * filled_blocks + "░" * (total_blocks - filled_blocks)

    def _calculate_progress_percent(self, downloaded_bytes: int, total_bytes: int | None) -> int:
        if not isinstance(total_bytes, int) or total_bytes <= 0:
            return -1
        return max(0, min(100, int(downloaded_bytes * 100 / total_bytes)))

    def _parse_content_length(self, content_length: str | None) -> int | None:
        if not isinstance(content_length, str) or not content_length.strip():
            return None
        try:
            parsed = int(content_length)
        except ValueError:
            return None
        return parsed if parsed >= 0 else None

    def _format_file_size(self, size: int | None) -> str:
        if not isinstance(size, int) or size < 0:
            return "未知"

        value = float(size)
        units = ["B", "KB", "MB", "GB", "TB"]
        for unit in units:
            if value < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(value)} {unit}"
                return f"{value:.1f} {unit}"
            value /= 1024

    async def _open_remote_stream(
        self,
        *,
        client: httpx.AsyncClient,
        remote_url: str,
        request: Request,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if "range" in request.headers:
            headers["Range"] = request.headers["range"]

        try:
            return await self._open_remote_stream_with_retry(
                remote_url=remote_url,
                request_method=request.method.upper(),
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"代理 Telegram 文件失败：{exc}",
            ) from exc

        try:
            built_request = client.build_request(
                method=request.method.upper(),
                url=remote_url,
                headers=headers,
            )
            return await client.send(built_request, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"代理 Telegram 文件失败：{exc}",
            ) from exc

    async def _iter_stream(self, response: httpx.Response) -> AsyncIterator[bytes]:
        async for chunk in response.aiter_bytes(settings.proxy_chunk_size):
            if chunk:
                yield chunk

    def _apply_content_disposition(
        self,
        *,
        headers: dict[str, str],
        file_name: str,
        as_attachment: bool,
    ) -> None:
        disposition = "attachment" if as_attachment else "inline"
        headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(file_name)}"

    def _extract_supported_media(self, message: dict[str, Any]) -> TelegramIncomingMedia | None:
        video = message.get("video")
        if isinstance(video, dict):
            return self._build_incoming_media(video)

        animation = message.get("animation")
        if isinstance(animation, dict):
            return self._build_incoming_media(animation)

        video_note = message.get("video_note")
        if isinstance(video_note, dict):
            return self._build_incoming_media(video_note)

        document = message.get("document")
        if isinstance(document, dict) and self._is_supported_video_document(document):
            return self._build_incoming_media(document)
        return None

    def _extract_supported_url(self, message: dict[str, Any]) -> str | None:
        text = self._extract_message_text(message)
        if not isinstance(text, str) or not text.strip():
            return None

        for match in URL_PATTERN.findall(text):
            candidate = self._strip_url_punctuation(match)
            if not candidate:
                continue
            try:
                normalized = task_service.normalize_source_url(candidate)
                task_service.detect_platform(normalized)
            except HTTPException:
                continue
            return normalized
        return None

    def _contains_url_text(self, message: dict[str, Any]) -> bool:
        text = self._extract_message_text(message)
        if not isinstance(text, str):
            return False
        return bool(URL_PATTERN.search(text))

    def _extract_message_text(self, message: dict[str, Any]) -> str | None:
        for key in ("text", "caption"):
            value = message.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return None

    def _strip_url_punctuation(self, value: str) -> str:
        return value.strip().rstrip(").,!?]>}\"'")

    def _contains_media_payload(self, message: dict[str, Any]) -> bool:
        # Telegram 里很多“看起来像视频”的资源，实际会以 animation 或 video_note 下发。
        media_keys = {
            "video",
            "document",
            "animation",
            "video_note",
            "audio",
            "voice",
            "photo",
            "sticker",
        }
        return any(key in message for key in media_keys)

    def _build_incoming_media(self, payload: dict[str, Any]) -> TelegramIncomingMedia | None:
        telegram_file_id = payload.get("file_id")
        telegram_file_unique_id = payload.get("file_unique_id")
        if not isinstance(telegram_file_id, str) or not telegram_file_id:
            return None
        if not isinstance(telegram_file_unique_id, str) or not telegram_file_unique_id:
            return None

        content_type = payload.get("mime_type")
        if not isinstance(content_type, str) or not content_type:
            file_name = payload.get("file_name")
            guessed_type = mimetypes.guess_type(file_name)[0] if isinstance(file_name, str) else None
            content_type = guessed_type or "video/mp4"

        file_name = self._build_public_file_name(payload=payload, content_type=content_type)
        file_size = payload.get("file_size")
        if not isinstance(file_size, int):
            file_size = None

        return TelegramIncomingMedia(
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            file_name=file_name,
            content_type=content_type,
            file_size=file_size,
        )

    def _build_public_file_name(self, payload: dict[str, Any], content_type: str) -> str:
        raw_name = payload.get("file_name")
        if isinstance(raw_name, str) and raw_name.strip():
            return build_public_file_name(raw_name, fallback_stem="telegram-video")

        guessed_extension = mimetypes.guess_extension(content_type) or ".mp4"
        return build_public_file_name(
            f"telegram-video{guessed_extension}",
            fallback_stem="telegram-video",
        )

    def _is_supported_video_document(self, payload: dict[str, Any]) -> bool:
        mime_type = payload.get("mime_type")
        if isinstance(mime_type, str) and mime_type.startswith("video/"):
            return True

        file_name = payload.get("file_name")
        if isinstance(file_name, str):
            return Path(file_name).suffix.lower() in VIDEO_DOCUMENT_EXTENSIONS
        return False

    def _extract_update_message(self, update: dict[str, Any]) -> dict[str, Any] | None:
        for key in ("message", "edited_message", "channel_post", "edited_channel_post"):
            value = update.get(key)
            if isinstance(value, dict):
                return value
        return None

    def _extract_chat_id(self, message: dict[str, Any]) -> int | None:
        chat = message.get("chat")
        if not isinstance(chat, dict):
            return None
        chat_id = chat.get("id")
        if isinstance(chat_id, int):
            return chat_id
        return None

    def _extract_message_id(self, message: dict[str, Any]) -> int | None:
        message_id = message.get("message_id")
        if isinstance(message_id, int):
            return message_id
        return None

    def _is_forwarded_message(self, message: dict[str, Any]) -> bool:
        return isinstance(message.get("forward_origin"), dict)

    def _is_chat_allowed(self, chat_id: int) -> bool:
        allowed_chat_ids = settings.telegram_allowed_chat_id_set
        if not allowed_chat_ids:
            return True
        return chat_id in allowed_chat_ids

    def _build_api_url(self, method: str) -> str:
        if not settings.telegram_bot_configured:
            raise TelegramServiceError("Telegram Bot Token 未配置。")
        token = settings.telegram_bot_token.strip()
        return f"{settings.telegram_bot_api_base}/bot{token}/{method}"

    def _build_file_download_url(self, file_path: str) -> str:
        if not settings.telegram_bot_configured:
            raise TelegramServiceError("Telegram Bot Token 未配置。")
        token = settings.telegram_bot_token.strip()
        normalized_path = quote(file_path.lstrip("/"), safe="/")
        return f"{settings.telegram_bot_api_base}/file/bot{token}/{normalized_path}"

    def _build_http_client(self, *, read_timeout: float) -> httpx.AsyncClient:
        timeout = httpx.Timeout(
            connect=settings.proxy_timeout_seconds,
            read=read_timeout,
            write=settings.proxy_timeout_seconds,
            pool=settings.proxy_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=max(5, settings.proxy_max_connections),
            max_keepalive_connections=0,
            keepalive_expiry=5.0,
        )
        return httpx.AsyncClient(timeout=timeout, limits=limits)

    def _generate_public_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(12))

    def _get_api_client(self, client_kind: str) -> httpx.AsyncClient:
        if client_kind == "poll":
            return self._require_poll_client()
        if client_kind == "file":
            return self._require_file_client()
        return self._require_command_client()

    def _require_poll_client(self) -> httpx.AsyncClient:
        if self._poll_client is None:
            raise TelegramServiceError("Telegram polling HTTP client is not started.")
        return self._poll_client

    def _require_command_client(self) -> httpx.AsyncClient:
        if self._command_client is None:
            raise TelegramServiceError("Telegram command HTTP client is not started.")
        return self._command_client

    def _require_file_client(self) -> httpx.AsyncClient:
        if self._file_client is None:
            raise TelegramServiceError("Telegram file HTTP client is not started.")
        return self._file_client

    def _require_stream_client(self) -> httpx.AsyncClient:
        if self._stream_client is None:
            raise TelegramServiceError("Telegram stream HTTP client is not started.")
        return self._stream_client

    def _require_client(self) -> httpx.AsyncClient:
        return self._require_command_client()

    def _load_index(self) -> dict[str, TelegramPublicFile]:
        index_path = settings.telegram_file_index_path
        if not index_path.exists():
            return {}

        try:
            raw_data = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("failed to load telegram file index: %s", index_path)
            return {}

        if not isinstance(raw_data, list):
            return {}

        files: dict[str, TelegramPublicFile] = {}
        for item in raw_data:
            if not isinstance(item, dict):
                continue

            try:
                public_id = str(item["public_id"])
                telegram_file_id = str(item["telegram_file_id"])
                telegram_file_unique_id = str(item["telegram_file_unique_id"])
                file_name = str(item["file_name"])
                content_type = str(item["content_type"])
                source_chat_id = int(item["source_chat_id"])
                source_message_id = int(item["source_message_id"])
                created_at = datetime.fromisoformat(item["created_at"])
                updated_at = datetime.fromisoformat(item["updated_at"])
                last_accessed_at = datetime.fromisoformat(item["last_accessed_at"])
            except (KeyError, TypeError, ValueError):
                continue

            file_path = item.get("file_path")
            file_size = item.get("file_size")
            cached_output_file_id = item.get("cached_output_file_id")
            if not isinstance(file_path, str):
                file_path = None
            if not isinstance(file_size, int):
                file_size = None
            if not isinstance(cached_output_file_id, str):
                cached_output_file_id = None

            files[public_id] = TelegramPublicFile(
                public_id=public_id,
                telegram_file_id=telegram_file_id,
                telegram_file_unique_id=telegram_file_unique_id,
                file_path=file_path,
                cached_output_file_id=cached_output_file_id,
                file_name=file_name,
                content_type=content_type,
                file_size=file_size,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                created_at=created_at,
                updated_at=updated_at,
                last_accessed_at=last_accessed_at,
            )
        return files

    def _persist_index_unlocked(self) -> None:
        index_path = settings.telegram_file_index_path
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "public_id": item.public_id,
                "telegram_file_id": item.telegram_file_id,
                "telegram_file_unique_id": item.telegram_file_unique_id,
                "file_path": item.file_path,
                "cached_output_file_id": item.cached_output_file_id,
                "file_name": item.file_name,
                "content_type": item.content_type,
                "file_size": item.file_size,
                "source_chat_id": item.source_chat_id,
                "source_message_id": item.source_message_id,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
                "last_accessed_at": item.last_accessed_at.isoformat(),
            }
            for item in self._files.values()
        ]
        index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _should_refresh_public_file_access(self, public_id: str) -> bool:
        interval_seconds = max(0, settings.media_access_refresh_interval_seconds)
        if interval_seconds == 0:
            return True
        return monotonic() >= self._access_refresh_after.get(public_id, 0.0)

    def _mark_public_file_access_refreshed(self, public_id: str) -> None:
        interval_seconds = max(0, settings.media_access_refresh_interval_seconds)
        if interval_seconds == 0:
            self._access_refresh_after.pop(public_id, None)
            return
        self._access_refresh_after[public_id] = monotonic() + interval_seconds

    def _build_unique_index(self, files: dict[str, TelegramPublicFile]) -> dict[str, str]:
        index: dict[str, str] = {}
        for public_id, public_file in files.items():
            index[public_file.telegram_file_unique_id] = public_id
        return index

    def _load_state(self) -> int:
        state_path = settings.telegram_state_path
        if not state_path.exists():
            return 0

        try:
            raw_data = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("failed to load telegram bot state: %s", state_path)
            return 0

        offset = raw_data.get("update_offset")
        if isinstance(offset, int) and offset >= 0:
            return offset
        return 0

    def _persist_state(self) -> None:
        state_path = settings.telegram_state_path
        state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"update_offset": self._update_offset}
        state_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


telegram_service = TelegramService()
