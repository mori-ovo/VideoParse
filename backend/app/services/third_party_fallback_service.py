import json
import re
import hashlib
import time
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    _twitter_status_api = "https://api.fxtwitter.com/i/status/{status_id}"
    _twitter_status_api_with_name = "https://api.fxtwitter.com/{screen_name}/status/{status_id}"
    _iiilab_extract_api = "https://service.iiilab.com/iiilab/extract"
    _iiilab_secret = "2HT8gjE3xL"

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

    def _extract_twitter_status_reference(self, url: str) -> tuple[str | None, str] | None:
        match = self._twitter_status_pattern.search(url)
        if match is None:
            return None
        screen_name = match.group("screen_name")
        if screen_name == "i":
            screen_name = None
        return screen_name, match.group("status_id")

    def _build_twitter_status_api_candidates(self, screen_name: str | None, status_id: str) -> list[str]:
        candidates = [self._twitter_status_api.format(status_id=status_id)]
        if screen_name:
            candidates.insert(
                0,
                self._twitter_status_api_with_name.format(screen_name=screen_name, status_id=status_id),
            )
        return candidates

    def _fetch_json(self, url: str) -> dict[str, object]:
        request = Request(
            url,
            headers={
                "User-Agent": "VideoParse/0.1",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=20) as response:
                raw_body = response.read().decode("utf-8", "ignore")
        except (HTTPError, URLError, TimeoutError) as exc:
            raise ThirdPartyFallbackError(f"third-party fallback request failed: {exc}") from exc

        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise ThirdPartyFallbackError("third-party fallback returned invalid JSON") from exc

        if not isinstance(payload, dict):
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
            extractor="iiilab",
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
