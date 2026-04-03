import asyncio
import importlib
import logging
import mimetypes
import shutil
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from app.core.config import settings
from app.schemas.task import Platform
from app.services.third_party_fallback_service import (
    ThirdPartyFallbackError,
    third_party_fallback_service,
)

logger = logging.getLogger(__name__)


class DownloaderUnavailableError(RuntimeError):
    pass


class DownloaderExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class DownloaderAvailability:
    yt_dlp_available: bool
    ffmpeg_available: bool


@dataclass(frozen=True)
class ExtractedMedia:
    title: str
    requires_merge: bool
    direct_playable: bool
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    extractor: str | None
    direct_url: str | None
    video_url: str | None
    audio_url: str | None
    direct_ext: str | None
    video_ext: str | None
    audio_ext: str | None
    direct_headers: dict[str, str]
    video_headers: dict[str, str]
    audio_headers: dict[str, str]


@dataclass(frozen=True)
class MediaTarget:
    url: str
    headers: dict[str, str]


@dataclass(frozen=True)
class DownloadProgressEvent:
    status: str
    progress: int
    message: str


@dataclass(frozen=True)
class DownloadedMedia:
    file_path: Path
    file_name: str
    content_type: str
    title: str
    requires_merge: bool
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    extractor: str | None


@dataclass(frozen=True)
class PlatformRequestOptions:
    platform: Platform | None
    proxy: str | None
    cookie_header: str | None
    cookies_file: Path | None
    user_agent: str | None


@dataclass(frozen=True)
class ExtractionAttempt:
    name: str
    request_options: PlatformRequestOptions
    extra_options: dict[str, Any]


class YtDlpLogger:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def debug(self, msg: str) -> None:
        return None

    def info(self, msg: str) -> None:
        return None

    def warning(self, msg: str) -> None:
        return None

    def error(self, msg: str) -> None:
        self.errors.append(msg)


class DownloaderService:
    def availability(self) -> DownloaderAvailability:
        return DownloaderAvailability(
            yt_dlp_available=self._is_yt_dlp_available(),
            ffmpeg_available=self._is_ffmpeg_available(),
        )

    async def extract_metadata(self, url: str) -> ExtractedMedia:
        return await asyncio.to_thread(self._extract_metadata_sync, url)

    async def resolve_media_target(self, url: str, kind: str) -> MediaTarget:
        metadata = await self.extract_metadata(url)
        if kind == "single" and metadata.direct_url:
            return MediaTarget(url=metadata.direct_url, headers=metadata.direct_headers)
        if kind == "video" and metadata.video_url:
            return MediaTarget(url=metadata.video_url, headers=metadata.video_headers)
        if kind == "audio" and metadata.audio_url:
            return MediaTarget(url=metadata.audio_url, headers=metadata.audio_headers)

        raise DownloaderExecutionError("未能解析到可用的媒体目标地址。")

    async def download(
        self,
        task_id: str,
        url: str,
        progress_callback: Callable[[DownloadProgressEvent], None] | None = None,
    ) -> DownloadedMedia:
        return await asyncio.to_thread(
            self._download_sync,
            task_id,
            url,
            progress_callback,
        )

    def _extract_metadata_sync(self, url: str) -> ExtractedMedia:
        try:
            normalized = self._extract_info_sync(
                task_id="metadata",
                url=url,
                download=False,
                progress_callback=None,
            )
            return self._build_extracted_media(normalized)
        except (DownloaderExecutionError, DownloaderUnavailableError):
            fallback_media = self._resolve_third_party_metadata(url)
            if fallback_media is not None:
                return fallback_media
            raise

    def _download_sync(
        self,
        task_id: str,
        url: str,
        progress_callback: Callable[[DownloadProgressEvent], None] | None,
    ) -> DownloadedMedia:
        task_output_dir = settings.output_dir / task_id
        task_temp_dir = settings.temp_dir / task_id
        task_output_dir.mkdir(parents=True, exist_ok=True)
        task_temp_dir.mkdir(parents=True, exist_ok=True)

        normalized = self._extract_info_sync(
            task_id=task_id,
            url=url,
            download=True,
            progress_callback=progress_callback,
        )

        media_path = self._find_downloaded_media_file(task_output_dir)
        if media_path is None:
            raise DownloaderExecutionError("yt-dlp 执行完成，但没有找到最终输出文件。")

        content_type = mimetypes.guess_type(media_path.name)[0] or "application/octet-stream"
        extracted = self._build_extracted_media(normalized)
        return DownloadedMedia(
            file_path=media_path,
            file_name=media_path.name,
            content_type=content_type,
            title=extracted.title,
            requires_merge=extracted.requires_merge,
            uploader=extracted.uploader,
            duration=extracted.duration,
            thumbnail=extracted.thumbnail,
            extractor=extracted.extractor,
        )

    def _extract_info_sync(
        self,
        task_id: str,
        url: str,
        download: bool,
        progress_callback: Callable[[DownloadProgressEvent], None] | None,
    ) -> dict[str, Any]:
        yt_dlp = self._load_yt_dlp_module()
        logger = YtDlpLogger()
        request_options = self._resolve_platform_request_options(url)
        attempts = self._build_attempts(request_options)

        last_exc: Exception | None = None
        last_message = "未能提取媒体信息。"

        for attempt in attempts:
            base_options = self._build_options(
                task_id=task_id,
                logger=logger,
                progress_callback=progress_callback,
                download=download,
                request_options=attempt.request_options,
            )
            options = self._merge_options(base_options, attempt.extra_options)

            try:
                info = self._extract_info_with_format_fallback(
                    yt_dlp_module=yt_dlp,
                    options=options,
                    logger=logger,
                    url=url,
                    download=download,
                )
                normalized = self._normalize_info(info)
                if not download and not self._has_usable_media(normalized):
                    last_message = "提取成功，但没有拿到可用的视频或音频地址。"
                    continue
                return normalized
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                last_message = logger.errors[-1] if logger.errors else str(exc)
                logger.errors.clear()
                continue

        if last_exc is not None:
            raise DownloaderExecutionError(self._build_guided_error_message(url, last_message)) from last_exc
        raise DownloaderExecutionError(self._build_guided_error_message(url, last_message))

    def _build_attempts(self, request_options: PlatformRequestOptions) -> list[ExtractionAttempt]:
        attempts: list[ExtractionAttempt] = [
            ExtractionAttempt(
                name="default",
                request_options=request_options,
                extra_options={},
            )
        ]
        platform = request_options.platform

        if platform == Platform.YOUTUBE:
            attempts.append(
                ExtractionAttempt(
                    name="youtube-incomplete",
                    request_options=request_options,
                    extra_options={
                        "extractor_args": {
                            "youtube": {
                                "formats": ["incomplete"],
                            }
                        }
                    },
                )
            )
            attempts.append(
                ExtractionAttempt(
                    name="youtube-ios",
                    request_options=request_options,
                    extra_options={
                        "extractor_args": {
                            "youtube": {
                                "player_client": ["ios"],
                                "formats": ["incomplete"],
                            }
                        }
                    },
                )
            )
            attempts.append(
                ExtractionAttempt(
                    name="youtube-android",
                    request_options=request_options,
                    extra_options={
                        "extractor_args": {
                            "youtube": {
                                "player_client": ["android"],
                                "formats": ["incomplete"],
                            }
                        }
                    },
                )
            )

        if platform == Platform.TWITTER:
            if request_options.cookie_header or request_options.cookies_file:
                guest_options = replace(request_options, cookie_header=None, cookies_file=None)
                attempts.extend(
                    [
                        ExtractionAttempt(
                            name="twitter-guest-default",
                            request_options=guest_options,
                            extra_options={},
                        ),
                        ExtractionAttempt(
                            name="twitter-guest-syndication",
                            request_options=guest_options,
                            extra_options={
                                "extractor_args": {
                                    "twitter": {
                                        "api": ["syndication"],
                                    }
                                }
                            },
                        ),
                        ExtractionAttempt(
                            name="twitter-guest-legacy",
                            request_options=guest_options,
                            extra_options={
                                "extractor_args": {
                                    "twitter": {
                                        "api": ["legacy"],
                                    }
                                }
                            },
                        ),
                    ]
                )
            else:
                attempts.extend(
                    [
                        ExtractionAttempt(
                            name="twitter-syndication",
                            request_options=request_options,
                            extra_options={
                                "extractor_args": {
                                    "twitter": {
                                        "api": ["syndication"],
                                    }
                                }
                            },
                        ),
                        ExtractionAttempt(
                            name="twitter-legacy",
                            request_options=request_options,
                            extra_options={
                                "extractor_args": {
                                    "twitter": {
                                        "api": ["legacy"],
                                    }
                                }
                            },
                        ),
                    ]
                )

        return self._dedupe_attempts(attempts)

    def _dedupe_attempts(self, attempts: list[ExtractionAttempt]) -> list[ExtractionAttempt]:
        unique: list[ExtractionAttempt] = []
        seen: set[str] = set()
        for attempt in attempts:
            marker = repr(
                (
                    attempt.request_options.platform,
                    attempt.request_options.proxy,
                    attempt.request_options.cookie_header,
                    str(attempt.request_options.cookies_file) if attempt.request_options.cookies_file else None,
                    attempt.request_options.user_agent,
                    attempt.extra_options,
                )
            )
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(attempt)
        return unique

    def _extract_info_with_format_fallback(
        self,
        yt_dlp_module: Any,
        options: dict[str, Any],
        logger: YtDlpLogger,
        url: str,
        download: bool,
    ) -> Any:
        try:
            with yt_dlp_module.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=download)
        except Exception as exc:  # noqa: BLE001
            message = logger.errors[-1] if logger.errors else str(exc)
            if "Requested format is not available" not in message or "format" not in options:
                raise

            fallback_options = dict(options)
            fallback_options.pop("format", None)
            logger.errors.clear()
            with yt_dlp_module.YoutubeDL(fallback_options) as ydl:
                return ydl.extract_info(url, download=download)

    def _build_options(
        self,
        task_id: str,
        logger: YtDlpLogger,
        progress_callback: Callable[[DownloadProgressEvent], None] | None,
        download: bool,
        request_options: PlatformRequestOptions,
    ) -> dict[str, Any]:
        output_dir = settings.output_dir / task_id
        temp_dir = settings.temp_dir / task_id
        options: dict[str, Any] = {
            "ignoreconfig": True,
            "merge_output_format": settings.merge_output_format,
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
            "cachedir": str(settings.cache_dir),
            "paths": {
                "home": str(output_dir),
                "temp": str(temp_dir),
            },
            "outtmpl": {
                "default": "%(title).160B [%(id)s].%(ext)s",
            },
            "logger": logger,
        }

        if download and settings.download_format:
            options["format"] = settings.download_format

        if request_options.proxy:
            options["proxy"] = request_options.proxy

        http_headers: dict[str, str] = {}
        if request_options.user_agent:
            http_headers["User-Agent"] = request_options.user_agent
        if request_options.cookie_header:
            http_headers["Cookie"] = request_options.cookie_header
        if http_headers:
            options["http_headers"] = http_headers

        if request_options.cookies_file is not None:
            options["cookiefile"] = str(request_options.cookies_file)

        ffmpeg_location = self._resolve_ffmpeg_location()
        if ffmpeg_location is not None:
            options["ffmpeg_location"] = ffmpeg_location

        configured_extractor_args = self._build_configured_extractor_args(request_options.platform)
        if configured_extractor_args:
            options["extractor_args"] = configured_extractor_args

        if download and progress_callback is not None:
            options["progress_hooks"] = [self._build_progress_hook(progress_callback)]

        return options

    def _resolve_third_party_metadata(self, url: str) -> ExtractedMedia | None:
        platform = self._detect_platform(url)
        if platform != Platform.TWITTER:
            return None

        try:
            media = third_party_fallback_service.resolve_twitter_media(url)
        except ThirdPartyFallbackError as exc:
            logger.warning("twitter third-party fallback failed: %s", exc)
            return None

        if media is None:
            return None

        return ExtractedMedia(
            title=media.title,
            requires_merge=False,
            direct_playable=True,
            uploader=media.uploader,
            duration=media.duration,
            thumbnail=media.thumbnail,
            extractor=media.extractor,
            direct_url=media.direct_url,
            video_url=None,
            audio_url=None,
            direct_ext=media.direct_ext,
            video_ext=None,
            audio_ext=None,
            direct_headers={},
            video_headers={},
            audio_headers={},
        )

    def _build_configured_extractor_args(
        self,
        platform: Platform | None,
    ) -> dict[str, dict[str, list[str]]] | None:
        if platform != Platform.YOUTUBE:
            return None

        youtube_args: dict[str, list[str]] = {}
        player_clients = self._split_csv(getattr(settings, "youtube_player_client", None))
        if player_clients:
            youtube_args["player_client"] = player_clients

        po_token = getattr(settings, "youtube_po_token", None)
        if isinstance(po_token, str) and po_token.strip():
            youtube_args["po_token"] = [po_token.strip()]

        if not youtube_args:
            return None
        return {"youtube": youtube_args}

    def _merge_options(self, base_options: dict[str, Any], extra_options: dict[str, Any]) -> dict[str, Any]:
        if not extra_options:
            return dict(base_options)

        merged = dict(base_options)
        for key, value in extra_options.items():
            if key == "extractor_args":
                current_args = dict(merged.get("extractor_args") or {})
                for ie_key, ie_args in value.items():
                    current_ie_args = dict(current_args.get(ie_key) or {})
                    for arg_key, arg_values in ie_args.items():
                        current_ie_args[arg_key] = list(arg_values)
                    current_args[ie_key] = current_ie_args
                merged["extractor_args"] = current_args
            else:
                merged[key] = value
        return merged

    def _split_csv(self, value: str | None) -> list[str]:
        if not isinstance(value, str):
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    def _resolve_platform_request_options(self, url: str) -> PlatformRequestOptions:
        platform = self._detect_platform(url)
        proxy = settings.proxy
        cookie_header = self._build_default_cookie_header()
        cookies_file = settings.cookies_file

        if platform == Platform.BILIBILI:
            proxy = settings.bilibili_proxy or proxy
            cookie_header = self._build_bilibili_cookie_header() or cookie_header
            cookies_file = settings.bilibili_cookies_file or cookies_file
        elif platform == Platform.YOUTUBE:
            cookie_header = self._normalize_cookie_header(settings.youtube_cookies) or cookie_header
            cookies_file = settings.youtube_cookies_file or cookies_file
        elif platform == Platform.TWITTER:
            cookie_header = self._build_twitter_cookie_header() or cookie_header
            cookies_file = settings.twitter_cookies_file or cookies_file

        resolved_cookie_file = None if cookie_header else self._resolve_cookie_file(cookies_file, platform)
        return PlatformRequestOptions(
            platform=platform,
            proxy=proxy,
            cookie_header=cookie_header,
            cookies_file=resolved_cookie_file,
            user_agent=settings.user_agent,
        )

    def _build_default_cookie_header(self) -> str | None:
        return self._normalize_cookie_header(settings.cookies)

    def _build_bilibili_cookie_header(self) -> str | None:
        direct_value = self._normalize_cookie_header(settings.bilibili_cookies)
        if direct_value:
            return direct_value
        return self._join_cookie_pairs(
            {
                "SESSDATA": settings.bilibili_sessdata,
                "bili_jct": settings.bilibili_bili_jct,
                "DedeUserID": settings.bilibili_dedeuserid,
            }
        )

    def _build_twitter_cookie_header(self) -> str | None:
        direct_value = self._normalize_cookie_header(settings.twitter_cookies)
        if direct_value:
            return direct_value
        return self._join_cookie_pairs(
            {
                "auth_token": settings.twitter_auth_token,
                "ct0": settings.twitter_ct0,
            }
        )

    def _join_cookie_pairs(self, cookie_map: dict[str, str | None]) -> str | None:
        pairs = [
            f"{key}={value.strip()}"
            for key, value in cookie_map.items()
            if isinstance(value, str) and value.strip()
        ]
        if not pairs:
            return None
        return "; ".join(pairs)

    def _normalize_cookie_header(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if normalized.lower().startswith("cookie:"):
            normalized = normalized.split(":", 1)[1].strip()
        return normalized or None

    def _resolve_cookie_file(self, configured_path: str | None, platform: Platform | None) -> Path | None:
        if not configured_path:
            return None

        cookie_path = Path(configured_path).expanduser()
        if not cookie_path.exists():
            platform_name = platform.value if platform is not None else "default"
            raise DownloaderUnavailableError(
                f"{platform_name} cookies 文件不存在：{cookie_path}。请检查 backend/.env 中的配置。"
            )
        return cookie_path

    def _detect_platform(self, url: str) -> Platform | None:
        host = urlparse(url).netloc.lower()
        if "bilibili.com" in host or "b23.tv" in host:
            return Platform.BILIBILI
        if "douyin.com" in host or "iesdouyin.com" in host:
            return Platform.DOUYIN
        if "twitter.com" in host or "x.com" in host:
            return Platform.TWITTER
        if "youtube.com" in host or "youtu.be" in host:
            return Platform.YOUTUBE
        if "reddit.com" in host or "redd.it" in host:
            return Platform.REDDIT
        return None

    def _build_guided_error_message(self, url: str, raw_message: str) -> str:
        message = raw_message.strip()
        platform = self._detect_platform(url)
        hints: list[str] = []

        if platform == Platform.BILIBILI and "412" in message:
            if not (settings.bilibili_proxy or settings.proxy):
                hints.append("可配置 BILIBILI_PROXY=socks5://your-proxy-host:1080")
            if not (
                settings.bilibili_cookies
                or settings.bilibili_sessdata
                or settings.cookies
                or settings.bilibili_cookies_file
                or settings.cookies_file
            ):
                hints.append("可配置 BILIBILI_SESSDATA 或 BILIBILI_COOKIES")

        if platform == Platform.YOUTUBE:
            if "Sign in to confirm you" in message and not (
                settings.youtube_cookies
                or settings.cookies
                or settings.youtube_cookies_file
                or settings.cookies_file
            ):
                hints.append("可配置 YOUTUBE_COOKIES，建议直接使用浏览器完整 Cookie 串")
            if "Requested format is not available" in message:
                hints.append("可尝试清空自定义 DOWNLOAD_FORMAT，或补充 YOUTUBE_PO_TOKEN")

        if platform == Platform.TWITTER and "No video could be found in this tweet" in message:
            hints.append("当前已自动尝试 guest / syndication / legacy 回退；若仍失败，说明该推文并非标准公开媒体接口可见")
            if not (
                settings.twitter_cookies
                or settings.twitter_auth_token
                or settings.cookies
                or settings.twitter_cookies_file
                or settings.cookies_file
            ):
                hints.append("可配置 TWITTER_AUTH_TOKEN，必要时补充 TWITTER_CT0")

        if not hints:
            return message
        return f"{message} 建议：{'；'.join(hints)}"

    def _build_progress_hook(
        self,
        progress_callback: Callable[[DownloadProgressEvent], None],
    ) -> Callable[[dict[str, Any]], None]:
        last_progress = 0

        def hook(data: dict[str, Any]) -> None:
            nonlocal last_progress
            status = data.get("status")
            if status != "downloading":
                return

            total = data.get("total_bytes") or data.get("total_bytes_estimate")
            downloaded = data.get("downloaded_bytes") or 0
            if total:
                percent = int(downloaded / total * 100)
                mapped_progress = min(85, max(35, 35 + int(percent * 0.5)))
            else:
                mapped_progress = min(85, max(last_progress, 40))

            if mapped_progress <= last_progress:
                return

            last_progress = mapped_progress
            progress_callback(
                DownloadProgressEvent(
                    status="downloading",
                    progress=mapped_progress,
                    message="正在通过 yt-dlp 下载媒体资源。",
                )
            )

        return hook

    def _build_extracted_media(self, info: dict[str, Any]) -> ExtractedMedia:
        formats = info.get("formats") or []
        progressive = self._select_playable_format(info, formats)
        best_video = self._select_best_video_format(formats)
        best_audio = self._select_best_audio_format(formats)
        return ExtractedMedia(
            title=info.get("title") or "未命名视频",
            requires_merge=self._requires_merge(info),
            direct_playable=progressive is not None,
            uploader=info.get("uploader") or info.get("channel"),
            duration=self._normalize_duration(info.get("duration")),
            thumbnail=info.get("thumbnail"),
            extractor=info.get("extractor_key") or info.get("extractor"),
            direct_url=progressive.get("url") if progressive else None,
            video_url=best_video.get("url") if best_video else None,
            audio_url=best_audio.get("url") if best_audio else None,
            direct_ext=progressive.get("ext") if progressive else None,
            video_ext=best_video.get("ext") if best_video else None,
            audio_ext=best_audio.get("ext") if best_audio else None,
            direct_headers=self._normalize_headers(progressive.get("http_headers")) if progressive else {},
            video_headers=self._normalize_headers(best_video.get("http_headers")) if best_video else {},
            audio_headers=self._normalize_headers(best_audio.get("http_headers")) if best_audio else {},
        )

    def _has_usable_media(self, info: dict[str, Any]) -> bool:
        extracted = self._build_extracted_media(info)
        return any([extracted.direct_url, extracted.video_url, extracted.audio_url])

    def _requires_merge(self, info: dict[str, Any]) -> bool:
        requested_formats = info.get("requested_formats") or []
        if len(requested_formats) > 1:
            return True

        formats = info.get("formats") or []
        has_video_only = any(
            item.get("vcodec") not in (None, "none") and item.get("acodec") == "none"
            for item in formats
        )
        has_audio_only = any(
            item.get("acodec") not in (None, "none") and item.get("vcodec") == "none"
            for item in formats
        )
        return has_video_only and has_audio_only

    def _normalize_info(self, info: Any) -> dict[str, Any]:
        if isinstance(info, dict) and info.get("entries"):
            entries = [entry for entry in info["entries"] if entry]
            if entries:
                return entries[0]
        if isinstance(info, dict):
            return info
        raise DownloaderExecutionError("yt-dlp 返回了无法识别的解析结果。")

    def _find_downloaded_media_file(self, task_output_dir: Path) -> Path | None:
        if not task_output_dir.exists():
            return None

        ignore_suffixes = {
            ".part",
            ".ytdl",
            ".temp",
            ".json",
            ".description",
            ".jpg",
            ".jpeg",
            ".png",
            ".webp",
            ".vtt",
            ".srt",
            ".ass",
            ".lrc",
        }
        candidates = [
            path
            for path in task_output_dir.rglob("*")
            if path.is_file() and path.suffix.lower() not in ignore_suffixes
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stat().st_mtime)

    def _select_progressive_format(self, formats: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("acodec") not in (None, "none")
            and item.get("vcodec") not in (None, "none")
            and self._is_direct_playable_protocol(item)
        ]
        if not candidates:
            return None
        return max(candidates, key=self._score_progressive_format)

    def _select_playable_format(
        self,
        info: dict[str, Any],
        formats: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        progressive = self._select_progressive_format(formats)
        if progressive is not None:
            return progressive

        info_protocol = str(info.get("protocol") or "").lower()
        info_url = info.get("url")
        if info_url and info_protocol in {"http", "https", "m3u8", "m3u8_native"}:
            return {
                "url": info_url,
                "ext": info.get("ext"),
                "protocol": info_protocol,
                "height": info.get("height"),
                "tbr": info.get("tbr"),
            }

        hls_candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("acodec") not in (None, "none")
            and item.get("vcodec") not in (None, "none")
            and self._is_hls_protocol(item)
        ]
        if hls_candidates:
            return max(hls_candidates, key=self._score_progressive_format)

        return None

    def _select_best_video_format(self, formats: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("vcodec") not in (None, "none")
            and item.get("acodec") == "none"
            and self._is_direct_playable_protocol(item)
        ]
        if not candidates:
            return None
        return max(candidates, key=self._score_video_format)

    def _select_best_audio_format(self, formats: list[dict[str, Any]]) -> dict[str, Any] | None:
        candidates = [
            item
            for item in formats
            if item.get("url")
            and item.get("acodec") not in (None, "none")
            and item.get("vcodec") == "none"
            and self._is_direct_playable_protocol(item)
        ]
        if not candidates:
            return None
        return max(candidates, key=self._score_audio_format)

    def _is_direct_playable_protocol(self, item: dict[str, Any]) -> bool:
        protocol = str(item.get("protocol") or "").lower()
        return protocol in {"http", "https", "m3u8", "m3u8_native"}

    def _is_hls_protocol(self, item: dict[str, Any]) -> bool:
        protocol = str(item.get("protocol") or "").lower()
        return protocol in {"m3u8", "m3u8_native"}

    def _score_progressive_format(self, item: dict[str, Any]) -> tuple[int, int, float]:
        ext_score = 2 if item.get("ext") == "mp4" else 1
        height = int(item.get("height") or 0)
        bitrate = float(item.get("tbr") or 0)
        return (ext_score, height, bitrate)

    def _score_video_format(self, item: dict[str, Any]) -> tuple[int, float]:
        height = int(item.get("height") or 0)
        bitrate = float(item.get("tbr") or 0)
        return (height, bitrate)

    def _score_audio_format(self, item: dict[str, Any]) -> tuple[int, float]:
        ext_score = 2 if item.get("ext") in {"m4a", "mp4"} else 1
        bitrate = float(item.get("abr") or item.get("tbr") or 0)
        return (ext_score, bitrate)

    def _normalize_headers(self, headers: Any) -> dict[str, str]:
        if not isinstance(headers, dict):
            return {}
        return {str(key): str(value) for key, value in headers.items() if value is not None}

    def _normalize_duration(self, value: Any) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return max(0, int(round(value)))
        try:
            return max(0, int(round(float(value))))
        except (TypeError, ValueError):
            return None

    def _load_yt_dlp_module(self) -> Any:
        if not self._is_yt_dlp_available():
            raise DownloaderUnavailableError(
                "未安装 yt-dlp。请先在后端环境执行 `pip install -r backend/requirements.txt`。"
            )
        return importlib.import_module("yt_dlp")

    def _is_yt_dlp_available(self) -> bool:
        return importlib.util.find_spec("yt_dlp") is not None

    def _is_ffmpeg_available(self) -> bool:
        return self._resolve_ffmpeg_location() is not None

    def _resolve_ffmpeg_location(self) -> str | None:
        if settings.ffmpeg_location:
            configured_path = Path(settings.ffmpeg_location)
            if configured_path.exists():
                return str(configured_path)

        ffmpeg_binary = shutil.which("ffmpeg")
        return ffmpeg_binary


downloader_service = DownloaderService()
