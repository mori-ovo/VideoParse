import re
from collections.abc import Iterator

URL_PATTERN = re.compile(r"https?://[^\s]+", re.IGNORECASE)
PURE_BILIBILI_BV_PATTERN = re.compile(r"\b(?P<bvid>BV[0-9A-Za-z]{10})\b", re.IGNORECASE)


def normalize_source_url_text(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        return normalized

    pure_bv_match = PURE_BILIBILI_BV_PATTERN.fullmatch(normalized)
    if pure_bv_match is not None:
        return build_bilibili_video_url(pure_bv_match.group("bvid"))

    first_candidate = next(iter_source_candidates(normalized), None)
    if first_candidate:
        return first_candidate
    return normalized


def iter_source_candidates(text: str) -> Iterator[str]:
    for match in URL_PATTERN.findall(text):
        candidate = strip_url_punctuation(match)
        if candidate:
            yield candidate

    for match in PURE_BILIBILI_BV_PATTERN.findall(text):
        candidate = match.strip()
        if candidate:
            yield build_bilibili_video_url(candidate)


def strip_url_punctuation(value: str) -> str:
    return value.strip().rstrip(").,!?]>}\"'")


def build_bilibili_video_url(bvid: str) -> str:
    return f"https://www.bilibili.com/video/{bvid}"
