import asyncio
import json
import logging
import mimetypes
import secrets
import string
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import FileResponse, Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.services.storage_service import storage_service
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


class TelegramService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._polling_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._lock = asyncio.Lock()
        self._files = self._load_index()
        self._unique_index = self._build_unique_index(self._files)
        self._update_offset = self._load_state()
        self._last_error: str | None = None
        self._poll_error_streak = 0

    async def start(self) -> None:
        if self._client is None:
            timeout = httpx.Timeout(
                connect=settings.proxy_timeout_seconds,
                read=max(settings.proxy_timeout_seconds, settings.telegram_poll_timeout_seconds + 5),
                write=settings.proxy_timeout_seconds,
                pool=settings.proxy_timeout_seconds,
            )
            limits = httpx.Limits(
                max_connections=max(5, settings.proxy_max_connections),
                max_keepalive_connections=max(5, settings.proxy_max_connections // 2),
            )
            self._client = httpx.AsyncClient(timeout=timeout, limits=limits)

        if not settings.telegram_bot_configured or not settings.telegram_polling_enabled:
            return

        if self._polling_task is not None and not self._polling_task.done():
            return

        self._stop_event = asyncio.Event()
        await self._delete_webhook()
        self._polling_task = asyncio.create_task(self._poll_updates_loop())

    async def stop(self) -> None:
        if self._polling_task is not None:
            self._stop_event.set()
            await self._polling_task
            self._polling_task = None

        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def status(self) -> dict[str, object]:
        return {
            "configured": settings.telegram_bot_configured,
            "polling_enabled": settings.telegram_polling_enabled,
            "polling_running": self._polling_task is not None and not self._polling_task.done(),
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

        resolved = await self._resolve_target(public_file=public_file, force_refresh=False)
        if resolved.local_path is not None:
            return FileResponse(
                path=resolved.local_path,
                media_type=resolved.content_type,
                filename=resolved.file_name,
                content_disposition_type="attachment" if as_attachment else "inline",
            )

        if resolved.remote_url is None:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Telegram 文件路径不可用，无法生成视频直链。",
            )

        client = self._require_client()
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
        async with self._lock:
            public_file = self._files.get(file_id)
            if public_file is None:
                return None

            public_file.last_accessed_at = datetime.now(timezone.utc)
            self._persist_index_unlocked()
            return public_file

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

            self._persist_index_unlocked()
            return len(expired_ids)

    async def get_link_by_message(self, message: dict[str, Any]) -> str | None:
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
        return storage_service.build_stream_url(public_file.public_id, public_file.file_name)

    async def handle_update(self, update: dict[str, Any]) -> None:
        message = self._extract_update_message(update)
        if message is None:
            return

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

        link = await self.get_link_by_message(message)
        if link is None:
            if self._contains_media_payload(message):
                await self._safe_send_message(
                    chat_id=chat_id,
                    text="只支持视频消息或 mime 为 video/* 的文件。",
                    reply_to_message_id=self._extract_message_id(message),
                )
            return

        await self._safe_send_message(
            chat_id=chat_id,
            text=link,
            reply_to_message_id=self._extract_message_id(message),
        )

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
                try:
                    await self.handle_update(update)
                except Exception:  # noqa: BLE001
                    logger.exception("failed to handle telegram update: %s", update_id)

    async def _sleep_until_next_poll(self) -> None:
        try:
            await asyncio.wait_for(
                self._stop_event.wait(),
                timeout=max(0, settings.telegram_poll_interval_seconds),
            )
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
            )
            self._clear_error()
        except Exception as exc:  # noqa: BLE001
            self._record_error(f"Failed to delete telegram webhook before polling: {exc}")
            logger.warning(
                "Telegram Bot API is unreachable at %s; webhook reset skipped. Check TELEGRAM_BOT_API_BASE or start telegram-bot-api.",
                settings.telegram_bot_api_base,
            )

    async def _register_media(
        self,
        *,
        media: TelegramIncomingMedia,
        chat_id: int,
        message_id: int,
    ) -> TelegramPublicFile:
        file_path = await self._get_file_path(media.telegram_file_id)
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
                    file_path=file_path,
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
                public_file.file_path = file_path
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

    async def _resolve_target(
        self,
        *,
        public_file: TelegramPublicFile,
        force_refresh: bool,
    ) -> TelegramResolvedTarget:
        file_path = public_file.file_path
        if force_refresh or not file_path:
            file_path = await self._refresh_file_path(public_file)

        if isinstance(file_path, str) and file_path:
            local_path = Path(file_path)
            # 本地 Bot API 在 local 模式下可能直接返回绝对路径，这种情况优先走本地文件响应。
            if local_path.is_absolute() and local_path.exists():
                return TelegramResolvedTarget(
                    file_name=public_file.file_name,
                    content_type=public_file.content_type,
                    local_path=local_path,
                )

            if not local_path.is_absolute():
                return TelegramResolvedTarget(
                    file_name=public_file.file_name,
                    content_type=public_file.content_type,
                    remote_url=self._build_file_download_url(file_path),
                )

        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Telegram 本地文件路径不可访问，请确认 Bot API 与当前服务部署在同一台机器。",
        )

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
        )
        if not isinstance(result, dict):
            raise TelegramServiceError("Telegram getFile 返回格式不正确。")

        file_path = result.get("file_path")
        if isinstance(file_path, str) and file_path:
            return file_path
        raise TelegramServiceError("Telegram getFile 没有返回 file_path。")

    async def _call_api(self, method: str, payload: dict[str, Any]) -> Any:
        client = self._require_client()
        url = self._build_api_url(method)
        try:
            response = await client.post(url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise TelegramServiceError(f"Telegram API 请求失败：{exc}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise TelegramServiceError("Telegram API 返回了无法解析的 JSON。") from exc

        if not isinstance(data, dict):
            raise TelegramServiceError("Telegram API 返回格式不正确。")
        if data.get("ok") is not True:
            description = data.get("description") or "unknown telegram api error"
            raise TelegramServiceError(f"Telegram API 调用失败：{description}")
        self._clear_error()
        return data.get("result")

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
            await self._call_api(method="sendMessage", payload=payload)
        except Exception:  # noqa: BLE001
            logger.exception("failed to send telegram message to chat_id=%s", chat_id)

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

    def _generate_public_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(12))

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise TelegramServiceError("Telegram HTTP 客户端尚未启动。")
        return self._client

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
            if not isinstance(file_path, str):
                file_path = None
            if not isinstance(file_size, int):
                file_size = None

            files[public_id] = TelegramPublicFile(
                public_id=public_id,
                telegram_file_id=telegram_file_id,
                telegram_file_unique_id=telegram_file_unique_id,
                file_path=file_path,
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
