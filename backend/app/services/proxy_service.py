import asyncio
import logging
import mimetypes
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.ffmpeg.merger import ffmpeg_merge_service
from app.schemas.task import ResultType
from app.services.downloader_service import MediaTarget, downloader_service
from app.services.task_service import task_service

logger = logging.getLogger(__name__)

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}

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

DEFAULT_PROXY_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/135.0.0.0 Safari/537.36"
)


@dataclass
class CachedProxyTarget:
    url: str
    headers: dict[str, str]
    expires_at: datetime


@dataclass
class PreparedMergedProcess:
    process: asyncio.subprocess.Process
    first_chunk: bytes
    stderr_task: asyncio.Task[str]
    stream_completed: bool = False


class ProxyService:
    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, CachedProxyTarget] = {}

    async def start(self) -> None:
        if self._client is not None:
            return

        timeout = httpx.Timeout(
            connect=settings.proxy_timeout_seconds,
            read=settings.proxy_timeout_seconds,
            write=settings.proxy_timeout_seconds,
            pool=settings.proxy_timeout_seconds,
        )
        limits = httpx.Limits(
            max_connections=settings.proxy_max_connections,
            max_keepalive_connections=max(5, settings.proxy_max_connections // 2),
        )
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=timeout,
            limits=limits,
        )

    async def stop(self) -> None:
        if self._client is None:
            return
        await self._client.aclose()
        self._client = None

    async def build_proxy_response(
        self,
        task_id: str,
        kind: str,
        request: Request,
    ) -> Response:
        task = await task_service.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="任务不存在。",
            )

        # 长视频 single 链接会在这里切进“ffmpeg 实时合流代理”分支。
        if self._should_use_merged_proxy(task=task, kind=kind):
            return await self._build_merged_proxy_response(task_id=task_id, request=request)

        client = self._require_client()
        upstream_response = await self._open_upstream_stream(
            client=client,
            task_id=task_id,
            kind=kind,
            request=request,
            force_refresh=False,
        )
        if upstream_response.status_code in {
            status.HTTP_401_UNAUTHORIZED,
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        }:
            await upstream_response.aclose()
            upstream_response = await self._open_upstream_stream(
                client=client,
                task_id=task_id,
                kind=kind,
                request=request,
                force_refresh=True,
            )

        filtered_headers = {
            key: value
            for key, value in upstream_response.headers.items()
            if key.lower() in PASS_RESPONSE_HEADERS and key.lower() not in HOP_BY_HOP_HEADERS
        }

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

    def _should_use_merged_proxy(self, task: object, kind: str) -> bool:
        if kind != "single":
            return False
        result = getattr(task, "result", None)
        if result is None:
            return False
        if getattr(result, "result_type", None) == ResultType.DOWNLOAD:
            return False
        return bool(result.video_url and result.audio_url and result.proxy_url)
        # 只有“无本地文件 + 同时拿到了 video/audio 两条分离流”才需要实时合流。
        return bool(result.video_url and result.audio_url and result.proxy_url)

    async def _build_merged_proxy_response(
        self,
        task_id: str,
        request: Request,
    ) -> Response:
        if not downloader_service.availability().ffmpeg_available:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="ffmpeg 不可用，当前无法生成单链接合流代理。",
            )

        content_type = mimetypes.guess_type(f"stream.{settings.merge_output_format}")[0] or "video/mp4"
        headers = {
            # 这类响应是按需生成的，不希望客户端或中间层长期缓存。
            "Cache-Control": "no-store",
            "Content-Type": content_type,
        }

        if request.method == "HEAD":
            return Response(content=b"", status_code=status.HTTP_200_OK, headers=headers)

        prepared: PreparedMergedProcess | None = None
        last_error: HTTPException | None = None
        for force_refresh in (False, True):
            try:
                # 第一次优先吃缓存；如果上游分离流已失效，再强制刷新元数据重试一次。
                prepared = await self._start_merged_process(
                    task_id=task_id,
                    force_refresh=force_refresh,
                )
                break
            except HTTPException as exc:
                last_error = exc
                if not force_refresh:
                    continue
                raise

        if prepared is None:
            raise last_error or HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="未能启动合流代理。",
            )

        return StreamingResponse(
            self._iter_merged_stream(prepared),
            status_code=status.HTTP_200_OK,
            headers=headers,
            background=BackgroundTask(self._close_merged_process, prepared),
        )

    async def _start_merged_process(
        self,
        task_id: str,
        force_refresh: bool,
    ) -> PreparedMergedProcess:
        video_target, audio_target = await self._resolve_merge_targets(
            task_id=task_id,
            force_refresh=force_refresh,
        )
        command = ffmpeg_merge_service.build_stream_merge_command(
            video_target=video_target,
            audio_target=audio_target,
        )

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"无法启动 ffmpeg 合流进程：{exc}",
            ) from exc

        assert process.stdout is not None
        assert process.stderr is not None
        stderr_task = asyncio.create_task(self._consume_process_stderr(process.stderr))
        try:
            # 先等到首个数据块，确保前端拿到 success 后链接确实能开始播放。
            first_chunk = await asyncio.wait_for(
                process.stdout.read(settings.proxy_chunk_size),
                timeout=settings.lazy_stream_startup_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            await self._terminate_process(process)
            stderr_output = await stderr_task
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail=f"合流代理启动超时：{stderr_output or 'ffmpeg did not produce output in time'}",
            ) from exc

        if not first_chunk:
            stderr_output = await self._close_failed_process(process, stderr_task)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"ffmpeg 未能产出可播放数据：{stderr_output or 'empty stream'}",
            )

        return PreparedMergedProcess(
            process=process,
            first_chunk=first_chunk,
            stderr_task=stderr_task,
        )

    async def _resolve_merge_targets(
        self,
        task_id: str,
        force_refresh: bool,
    ) -> tuple[MediaTarget, MediaTarget]:
        task = await task_service.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="任务不存在。",
            )

        metadata = await downloader_service.extract_metadata(
            task.source_url,
            force_refresh=force_refresh,
        )
        if metadata.direct_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前任务已经有单文件直链，无需走合流代理。",
            )
        if not metadata.video_url or not metadata.audio_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="当前任务没有可用于合流的音视频分离流。",
            )
        # ffmpeg 自己去拉上游流，因此这里要把 UA/Referer/Origin 一并传过去。
        return (
            MediaTarget(
                url=metadata.video_url,
                headers=self._build_merge_target_headers(
                    base_headers=metadata.video_headers,
                    source_url=task.source_url,
                ),
            ),
            MediaTarget(
                url=metadata.audio_url,
                headers=self._build_merge_target_headers(
                    base_headers=metadata.audio_headers,
                    source_url=task.source_url,
                ),
            ),
        )

    async def _open_upstream_stream(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        kind: str,
        request: Request,
        force_refresh: bool,
    ) -> httpx.Response:
        target = await self._resolve_proxy_target(task_id=task_id, kind=kind, force_refresh=force_refresh)
        task = await task_service.get_task(task_id)
        upstream_headers: dict[str, str] = dict(target.headers)
        if "range" in request.headers:
            upstream_headers["Range"] = request.headers["range"]
        self._apply_default_upstream_headers(
            headers=upstream_headers,
            request=request,
            source_url=task.source_url if task is not None else None,
        )

        method = request.method.upper()
        try:
            req = client.build_request(method=method, url=target.url, headers=upstream_headers)
            return await client.send(req, stream=True)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"代理上游媒体失败：{exc}",
            ) from exc

    def _apply_default_upstream_headers(
        self,
        headers: dict[str, str],
        request: Request,
        source_url: str | None,
    ) -> None:
        if not self._has_header(headers, "User-Agent"):
            headers["User-Agent"] = settings.user_agent or DEFAULT_PROXY_USER_AGENT

        if not self._has_header(headers, "Accept"):
            headers["Accept"] = request.headers.get("accept") or "*/*"

        if not source_url:
            return

        if not self._has_header(headers, "Referer"):
            headers["Referer"] = source_url

        origin = self._build_origin(source_url)
        if origin and not self._has_header(headers, "Origin"):
            headers["Origin"] = origin

    def _build_origin(self, source_url: str) -> str | None:
        parsed = urlparse(source_url)
        if not parsed.scheme or not parsed.netloc:
            return None
        return f"{parsed.scheme}://{parsed.netloc}"

    def _has_header(self, headers: dict[str, str], name: str) -> bool:
        target = name.lower()
        return any(key.lower() == target for key in headers)

    def _build_merge_target_headers(
        self,
        base_headers: dict[str, str],
        source_url: str,
    ) -> dict[str, str]:
        headers = dict(base_headers)
        if not self._has_header(headers, "User-Agent"):
            headers["User-Agent"] = settings.user_agent or DEFAULT_PROXY_USER_AGENT
        if not self._has_header(headers, "Referer"):
            headers["Referer"] = source_url
        origin = self._build_origin(source_url)
        if origin and not self._has_header(headers, "Origin"):
            headers["Origin"] = origin
        return headers

    async def _resolve_proxy_target(
        self,
        task_id: str,
        kind: str,
        force_refresh: bool,
    ) -> MediaTarget:
        cache_key = f"{task_id}:{kind}"
        now = datetime.now(timezone.utc)
        if not force_refresh:
            cached = self._cache.get(cache_key)
            if cached is not None and cached.expires_at > now:
                return MediaTarget(url=cached.url, headers=cached.headers)

        task = await task_service.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="任务不存在。",
            )

        target = await downloader_service.resolve_media_target(
            task.source_url,
            kind,
            force_refresh=force_refresh,
        )
        self._cache[cache_key] = CachedProxyTarget(
            url=target.url,
            headers=target.headers,
            expires_at=now + timedelta(minutes=5),
        )
        return target

    async def _iter_stream(self, response: httpx.Response) -> AsyncIterator[bytes]:
        async for chunk in response.aiter_bytes(settings.proxy_chunk_size):
            if chunk:
                yield chunk

    async def _iter_merged_stream(self, prepared: PreparedMergedProcess) -> AsyncIterator[bytes]:
        process = prepared.process
        stdout = process.stdout
        assert stdout is not None

        # 首块已经在预热阶段读出来了，这里先吐回去再继续读剩余数据。
        if prepared.first_chunk:
            yield prepared.first_chunk

        while True:
            chunk = await stdout.read(settings.proxy_chunk_size)
            if not chunk:
                prepared.stream_completed = True
                break
            yield chunk

    async def _consume_process_stderr(self, stream: asyncio.StreamReader) -> str:
        chunks: list[bytes] = []
        total_size = 0
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total_size += len(chunk)
            if total_size > 32768:
                merged = b"".join(chunks)[-32768:]
                chunks = [merged]
                total_size = len(merged)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    async def _close_failed_process(
        self,
        process: asyncio.subprocess.Process,
        stderr_task: asyncio.Task[str],
    ) -> str:
        await self._terminate_process(process)
        return await stderr_task

    async def _close_merged_process(self, prepared: PreparedMergedProcess) -> None:
        stop_reason = await self._terminate_process(prepared.process)
        stderr_output = await prepared.stderr_task
        if prepared.process.returncode in (0, None):
            return

        if stop_reason != "natural":
            logger.debug(
                "merged proxy ffmpeg stopped by proxy cleanup reason=%s code=%s stderr=%s",
                stop_reason,
                prepared.process.returncode,
                stderr_output,
            )
            return

        logger.warning(
            "merged proxy ffmpeg exited unexpectedly code=%s completed=%s stderr=%s",
            prepared.process.returncode,
            prepared.stream_completed,
            stderr_output,
        )

    async def _terminate_process_legacy(self, process: asyncio.subprocess.Process) -> str:
        if process.returncode is not None:
            return "natural"
        try:
            # 优先等待自然结束，只有超时才强制 kill，避免正常收尾被中断。
            await asyncio.wait_for(process.wait(), timeout=1)
        except asyncio.TimeoutError:
            process.kill()
            try:
                await process.wait()
            except ProcessLookupError:
                return
        except ProcessLookupError:
            return

    async def _terminate_process(self, process: asyncio.subprocess.Process) -> str:
        if process.returncode is not None:
            return "natural"

        try:
            await asyncio.wait_for(process.wait(), timeout=1)
            return "natural"
        except asyncio.TimeoutError:
            try:
                process.terminate()
            except ProcessLookupError:
                return "natural"

            try:
                await asyncio.wait_for(process.wait(), timeout=3)
                return "terminated"
            except asyncio.TimeoutError:
                try:
                    process.kill()
                except ProcessLookupError:
                    return "terminated"

                try:
                    await process.wait()
                except ProcessLookupError:
                    return "killed"
                return "killed"
        except ProcessLookupError:
            return "natural"

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="代理服务尚未启动。",
            )
        return self._client


proxy_service = ProxyService()
