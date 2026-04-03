import asyncio
import importlib
import mimetypes
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from app.core.config import settings
from app.schemas.task import Platform


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
        yt_dlp = self._load_yt_dlp_module()
        logger = YtDlpLogger()
        request_options = self._resolve_platform_request_options(url)
        options = self._build_options(
            task_id="metadata",
            logger=logger,
            progress_callback=None,
            download=False,
            request_options=request_options,
        )

        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001
            message = logger.errors[-1] if logger.errors else str(exc)
            raise DownloaderExecutionError(self._build_guided_error_message(url, message)) from exc

        normalized = self._normalize_info(info)
        return self._build_extracted_media(normalized)

    def _download_sync(
        self,
        task_id: str,
        url: str,
        progress_callback: Callable[[DownloadProgressEvent], None] | None,
    ) -> DownloadedMedia:
        yt_dlp = self._load_yt_dlp_module()
        task_output_dir = settings.output_dir / task_id
        task_temp_dir = settings.temp_dir / task_id
        task_output_dir.mkdir(parents=True, exist_ok=True)
        task_temp_dir.mkdir(parents=True, exist_ok=True)

        logger = YtDlpLogger()
        request_options = self._resolve_platform_request_options(url)
        options = self._build_options(
            task_id=task_id,
            logger=logger,
            progress_callback=progress_callback,
            download=True,
            request_options=request_options,
        )

        try:
            info = self._extract_info_with_format_fallback(yt_dlp, options, logger, url)
        except Exception as exc:  # noqa: BLE001
            message = logger.errors[-1] if logger.errors else str(exc)
            raise DownloaderExecutionError(self._build_guided_error_message(url, message)) from exc

        normalized = self._normalize_info(info)
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

    def _extract_info_with_format_fallback(
        self,
        yt_dlp_module: Any,
        options: dict[str, Any],
        logger: YtDlpLogger,
        url: str,
    ) -> Any:
        try:
            with yt_dlp_module.YoutubeDL(options) as ydl:
                return ydl.extract_info(url, download=True)
        except Exception as exc:  # noqa: BLE001
            message = logger.errors[-1] if logger.errors else str(exc)
            if "Requested format is not available" not in message or "format" not in options:
                raise

            fallback_options = dict(options)
            fallback_options.pop("format", None)
            logger.errors.clear()
            with yt_dlp_module.YoutubeDL(fallback_options) as ydl:
                return ydl.extract_info(url, download=True)

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

        if download and progress_callback is not None:
            options["progress_hooks"] = [self._build_progress_hook(progress_callback)]

        return options

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

        if platform == Platform.YOUTUBE and "Sign in to confirm you" in message:
            if not (
                settings.youtube_cookies
                or settings.cookies
                or settings.youtube_cookies_file
                or settings.cookies_file
            ):
                hints.append("可配置 YOUTUBE_COOKIES，建议直接使用浏览器完整 Cookie 串")

        if platform == Platform.TWITTER and "No video could be found in this tweet" in message:
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
            duration=info.get("duration"),
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
