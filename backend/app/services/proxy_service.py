from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, Request, status
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask

from app.core.config import settings
from app.services.downloader_service import MediaTarget, downloader_service
from app.services.task_service import task_service

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
            body = b""
            await upstream_response.aclose()
            return Response(
                content=body,
                status_code=upstream_response.status_code,
                headers=filtered_headers,
            )

        return StreamingResponse(
            self._iter_stream(upstream_response),
            status_code=upstream_response.status_code,
            headers=filtered_headers,
            background=BackgroundTask(upstream_response.aclose),
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

        target = await downloader_service.resolve_media_target(task.source_url, kind)
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

    def _require_client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="代理服务尚未启动。",
            )
        return self._client


proxy_service = ProxyService()
