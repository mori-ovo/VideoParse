import hashlib
import json
import mimetypes
import re
import time
import urllib.parse
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from app.core.config import settings


class ThirdPartyFallbackError(RuntimeError):
    pass


@dataclass(frozen=True)
class ThirdPartyMedia:
    title: str
    uploader: str | None
    duration: int | None
    thumbnail: str | None
    extractor: str
    direct_url: str
    direct_ext: str


class ThirdPartyFallbackService:
    _twitter_status_pattern = re.compile(r"/(?P<screen_name>[^/?#]+)/status/(?P<status_id>\d+)")
    _iwara_video_pattern = re.compile(r"/video/(?P<video_id>[A-Za-z0-9]+)")
    _twitter_status_api = "https://api.fxtwitter.com/i/status/{status_id}"
    _twitter_status_api_with_name = "https://api.fxtwitter.com/{screen_name}/status/{status_id}"
    _iwara_video_api = "https://api.iwara.tv/video/{video_id}"
    _iiilab_extract_api = "https://service.iiilab.com/iiilab/extract"
    _iiilab_secret = "2HT8gjE3xL"
    _iwara_version_secret = "5nFp9kmbNnHdAFhaqMvt"

    def resolve_twitter_media(self, url: str) -> ThirdPartyMedia | None:
        status_reference = self._extract_twitter_status_reference(url)
        if status_reference is None:
            return None

        screen_name, status_id = status_reference
        last_error: ThirdPartyFallbackError | None = None

        for api_url in self._build_twitter_status_api_candidates(screen_name, status_id):
            try:
                payload = self._fetch_json(api_url)
            except ThirdPartyFallbackError as exc:
                last_error = exc
                continue

            media = self._parse_fxtwitter_payload(payload, status_id)
            if media is not None:
                return media

        if last_error is not None:
            raise last_error
        return None

    def resolve_youtube_media(self, url: str) -> ThirdPartyMedia | None:
        payload = self._fetch_iiilab_payload(url=url, site="youtube")
        return self._parse_iiilab_youtube_payload(payload, url)

    def resolve_douyin_media(self, url: str) -> ThirdPartyMedia | None:
        payload = self._fetch_iiilab_payload(url=url, site="douyin")
        return self._parse_iiilab_generic_video_payload(payload, url, extractor="iiilab-douyin")

    def resolve_iwara_media(self, url: str) -> ThirdPartyMedia | None:
        video_id = self._extract_iwara_video_id(url)
        if video_id is None:
            return None

        # Iwara 当前对官方 API 做了 Cloudflare/会话保护。
        # 这里优先走官方 API 兜底，而不是继续依赖已经失效的 yt-dlp 内置提取器。
        video_payload = self._fetch_json(
            self._iwara_video_api.format(video_id=video_id),
            headers=self._build_iwara_headers(),
            invalid_json_message=(
                "iwara api returned non-json content; this usually means Cloudflare blocked the "
                "request or IWARA_AUTHORIZATION / IWARA_COOKIES is missing or expired"
            ),
        )

        message = video_payload.get("message")
        if isinstance(message, str) and message:
            raise ThirdPartyFallbackError(f"iwara api returned error: {message}")

        file_url = video_payload.get("fileUrl")
        if not isinstance(file_url, str) or not file_url:
            return None

        # 第二跳 fileUrl 会返回不同清晰度的文件列表，并要求额外的 X-Version 头。
        files_payload = self._fetch_json(
            file_url,
            headers=self._build_iwara_file_headers(file_url),
            invalid_json_message=(
                "iwara file api returned non-json content; Cloudflare/session protection is still active"
            ),
            expected_type=list,
        )

        media = self._parse_iwara_payload(video_payload, files_payload)
        if media is None:
            raise ThirdPartyFallbackError("iwara api returned no usable progressive file")
        return media

    def _extract_twitter_status_reference(self, url: str) -> tuple[str | None, str] | None:
        match = self._twitter_status_pattern.search(url)
        if match is None:
            return None
        screen_name = match.group("screen_name")
        if screen_name == "i":
            screen_name = None
        return screen_name, match.group("status_id")

    def _extract_iwara_video_id(self, url: str) -> str | None:
        match = self._iwara_video_pattern.search(url)
        if match is None:
            return None
        return match.group("video_id")

    def _build_twitter_status_api_candidates(self, screen_name: str | None, status_id: str) -> list[str]:
        candidates = [self._twitter_status_api.format(status_id=status_id)]
        if screen_name:
            candidates.insert(
                0,
                self._twitter_status_api_with_name.format(screen_name=screen_name, status_id=status_id),
            )
        return candidates

    def _fetch_json(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        *,
        invalid_json_message: str | None = None,
        expected_type: type[dict[str, object]] | type[list[object]] = dict,
    ) -> dict[str, object] | list[object]:
        request_headers = {
            "User-Agent": "VideoParse/0.1",
            "Accept": "application/json",
        }
        if headers:
            request_headers.update(headers)

        request = Request(url, headers=request_headers)

        try:
            with urlopen(request, timeout=20) as response:
                raw_body = response.read().decode("utf-8", "ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ThirdPartyFallbackError(f"third-party fallback request failed: {exc}") from exc

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ThirdPartyFallbackError(
                invalid_json_message or "third-party fallback returned invalid JSON"
            ) from exc

        if expected_type is dict and not isinstance(payload, dict):
            raise ThirdPartyFallbackError("third-party fallback returned unexpected payload")
        if expected_type is list and not isinstance(payload, list):
            raise ThirdPartyFallbackError("third-party fallback returned unexpected payload")
        return payload

    def _fetch_iiilab_payload(self, url: str, site: str) -> dict[str, object]:
        timestamp = str(int(time.time()))
        signature = hashlib.md5((url + site + timestamp + self._iiilab_secret).encode("utf-8")).hexdigest()
        request_body = json.dumps({"url": url, "site": site}).encode("utf-8")
        request = Request(
            self._iiilab_extract_api,
            data=request_body,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Accept": "application/json",
                "Content-Type": "application/json",
                "G-Timestamp": timestamp,
                "G-Footer": signature,
            },
        )

        try:
            with urlopen(request, timeout=20) as response:
                raw_body = response.read().decode("utf-8", "ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ThirdPartyFallbackError(f"iiilab fallback request failed: {exc}") from exc

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ThirdPartyFallbackError("iiilab fallback returned invalid JSON") from exc

        if not isinstance(payload, dict):
            raise ThirdPartyFallbackError("iiilab fallback returned unexpected payload")
        return payload

    def _build_iwara_headers(self) -> dict[str, str]:
        headers = {
            "User-Agent": settings.iwara_user_agent or settings.user_agent or "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.iwara.tv/",
            "Origin": "https://www.iwara.tv",
        }
        # Iwara 登录态通常不是传统 cookie，而是前端存储的 Bearer token。
        authorization = self._normalize_iwara_authorization(settings.iwara_authorization)
        if authorization:
            headers["Authorization"] = authorization
        cookies = self._normalize_cookie_header(settings.iwara_cookies)
        if cookies:
            headers["Cookie"] = cookies
        return headers

    def _build_iwara_file_headers(self, file_url: str) -> dict[str, str]:
        parsed = urllib.parse.urlparse(file_url)
        query = urllib.parse.parse_qs(parsed.query)
        expires = query.get("expires", [None])[0]
        path_parts = parsed.path.rstrip("/").split("/")
        if not expires or not path_parts:
            raise ThirdPartyFallbackError("iwara file url is missing expires parameter")

        # 这个签名算法来自 yt-dlp 旧版 Iwara 提取器，用于读取 fileUrl 返回的真实文件列表。
        x_version = hashlib.sha1(
            "_".join((path_parts[-1], expires, self._iwara_version_secret)).encode("utf-8")
        ).hexdigest()

        headers = self._build_iwara_headers()
        headers["X-Version"] = x_version
        return headers

    def _normalize_cookie_header(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip()
        if normalized.lower().startswith("cookie:"):
            normalized = normalized.split(":", 1)[1].strip()
        return normalized or None

    def _normalize_iwara_authorization(self, value: str | None) -> str | None:
        if not value:
            return None
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.lower().startswith("bearer "):
            return normalized
        return f"Bearer {normalized}"

    def _parse_fxtwitter_payload(
        self,
        payload: dict[str, object],
        status_id: str,
    ) -> ThirdPartyMedia | None:
        tweet = payload.get("tweet")
        if not isinstance(tweet, dict):
            return None

        media = tweet.get("media")
        if not isinstance(media, dict):
            return None

        videos = media.get("videos")
        if not isinstance(videos, list) or not videos:
            return None

        best_video: dict[str, object] | None = None
        best_variant: dict[str, object] | None = None
        best_score = (-1, -1, -1)

        for video in videos:
            if not isinstance(video, dict):
                continue

            variant = self._select_best_variant(video)
            if variant is None:
                continue

            score = (
                int(variant.get("bitrate") or 0),
                int(video.get("width") or 0),
                int(video.get("height") or 0),
            )
            if score > best_score:
                best_video = video
                best_variant = variant
                best_score = score

        if best_video is None or best_variant is None:
            return None

        title = self._normalize_title(tweet.get("text"), status_id)
        author = tweet.get("author")
        uploader = author.get("screen_name") if isinstance(author, dict) else None

        duration_value = best_video.get("duration")
        duration: int | None = None
        if isinstance(duration_value, (int, float)):
            duration = max(0, int(round(duration_value)))

        thumbnail = best_video.get("thumbnail_url")
        direct_url = best_variant.get("url")
        if not isinstance(direct_url, str) or not direct_url:
            return None

        return ThirdPartyMedia(
            title=title,
            uploader=uploader if isinstance(uploader, str) and uploader.strip() else None,
            duration=duration,
            thumbnail=thumbnail if isinstance(thumbnail, str) and thumbnail.strip() else None,
            extractor="fxtwitter",
            direct_url=direct_url,
            direct_ext="mp4",
        )

    def _parse_iwara_payload(
        self,
        video_payload: dict[str, object],
        files_payload: list[object],
    ) -> ThirdPartyMedia | None:
        best_file = self._select_best_iwara_file(files_payload)
        if best_file is None:
            return None

        title = video_payload.get("title")
        if not isinstance(title, str) or not title.strip():
            title = "iwara-video"

        user = video_payload.get("user")
        uploader = None
        if isinstance(user, dict):
            name = user.get("name")
            if isinstance(name, str) and name.strip():
                uploader = name.strip()

        thumbnail = None
        file_info = video_payload.get("file")
        if isinstance(file_info, dict):
            file_id = file_info.get("id")
            if isinstance(file_id, str) and file_id.strip():
                thumbnail = f"https://files.iwara.tv/image/thumbnail/{file_id}/thumbnail-00.jpg"

        return ThirdPartyMedia(
            title=" ".join(title.split())[:160],
            uploader=uploader,
            duration=self._normalize_duration(video_payload.get("duration")),
            thumbnail=thumbnail,
            extractor="iwara-api",
            direct_url=best_file["url"],
            direct_ext=best_file["ext"],
        )

    def _select_best_iwara_file(self, files_payload: list[object]) -> dict[str, str] | None:
        best_score = (-1, -1)
        best_result: dict[str, str] | None = None

        for item in files_payload:
            if not isinstance(item, dict):
                continue
            source = item.get("src")
            if not isinstance(source, dict):
                continue

            direct_url = source.get("view") or source.get("download")
            if not isinstance(direct_url, str) or not direct_url:
                continue

            normalized_url = self._normalize_proto_relative_url(direct_url)
            if not normalized_url:
                continue

            name = str(item.get("name") or "")
            # Source > 具体分辨率数值 > preview，其余未知命名排在中间。
            score = self._score_iwara_quality(name)
            if score <= best_score:
                continue

            ext = self._mimetype_to_extension(item.get("type"))
            best_score = score
            best_result = {
                "url": normalized_url,
                "ext": ext,
            }

        return best_result

    def _score_iwara_quality(self, value: str) -> tuple[int, int]:
        normalized = value.strip().lower()
        if normalized == "source":
            return (3, 9999)
        if normalized == "preview":
            return (0, 0)
        match = re.search(r"(\d+)", normalized)
        if match is None:
            return (1, 0)
        return (2, int(match.group(1)))

    def _mimetype_to_extension(self, value: object) -> str:
        if not isinstance(value, str) or not value.strip():
            return "mp4"
        guessed = mimetypes.guess_extension(value.strip(), strict=False)
        if not guessed:
            return "mp4"
        return guessed.lstrip(".")

    def _normalize_proto_relative_url(self, value: str) -> str | None:
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.startswith("//"):
            return f"https:{normalized}"
        return normalized

    def _normalize_duration(self, value: object) -> int | None:
        if value is None:
            return None
        if isinstance(value, (int, float)):
            return max(0, int(round(value)))
        try:
            return max(0, int(round(float(str(value)))))
        except (TypeError, ValueError):
            return None

    def _select_best_variant(self, video: dict[str, object]) -> dict[str, object] | None:
        variants = video.get("variants")
        if not isinstance(variants, list):
            variants = []

        mp4_variants: list[dict[str, object]] = []
        for variant in variants:
            if not isinstance(variant, dict):
                continue
            content_type = str(variant.get("content_type") or "")
            url = variant.get("url")
            if "mp4" not in content_type or not isinstance(url, str) or not url:
                continue
            mp4_variants.append(variant)

        if mp4_variants:
            return max(mp4_variants, key=lambda item: int(item.get("bitrate") or 0))

        direct_url = video.get("url")
        if isinstance(direct_url, str) and direct_url.endswith(".mp4"):
            return {
                "url": direct_url,
                "bitrate": 0,
            }

        return None

    def _normalize_title(self, value: object, status_id: str) -> str:
        if isinstance(value, str):
            normalized = " ".join(value.split())
            if normalized:
                return normalized[:160]
        return f"twitter-{status_id}"

    def _parse_iiilab_youtube_payload(
        self,
        payload: dict[str, object],
        source_url: str,
    ) -> ThirdPartyMedia | None:
        return self._parse_iiilab_generic_video_payload(payload, source_url, extractor="iiilab")

    def _parse_iiilab_generic_video_payload(
        self,
        payload: dict[str, object],
        source_url: str,
        *,
        extractor: str,
    ) -> ThirdPartyMedia | None:
        medias = payload.get("medias")
        if not isinstance(medias, list):
            return None

        primary_media: dict[str, object] | None = None
        for media in medias:
            if not isinstance(media, dict):
                continue
            if media.get("media_type") == "video":
                primary_media = media
                break

        if primary_media is None:
            return None

        direct_url = primary_media.get("resource_url")
        if not isinstance(direct_url, str) or not direct_url:
            direct_url = self._select_iiilab_progressive_url(primary_media)
        if not isinstance(direct_url, str) or not direct_url:
            return None

        title = payload.get("text")
        if not isinstance(title, str) or not title.strip():
            title = source_url

        thumbnail = primary_media.get("preview_url")

        return ThirdPartyMedia(
            title=" ".join(title.split())[:160],
            uploader=None,
            duration=None,
            thumbnail=thumbnail if isinstance(thumbnail, str) and thumbnail.strip() else None,
            extractor=extractor,
            direct_url=direct_url,
            direct_ext="mp4",
        )

    def _select_iiilab_progressive_url(self, media: dict[str, object]) -> str | None:
        formats = media.get("formats")
        if not isinstance(formats, list):
            return None

        candidates: list[tuple[int, str]] = []
        for item in formats:
            if not isinstance(item, dict):
                continue
            if int(item.get("separate") or 0) != 0:
                continue
            video_url = item.get("video_url")
            if not isinstance(video_url, str) or not video_url:
                continue
            quality = int(item.get("quality") or 0)
            candidates.append((quality, video_url))

        if not candidates:
            return None
        return max(candidates, key=lambda entry: entry[0])[1]


third_party_fallback_service = ThirdPartyFallbackService()
