import html
import re
from pathlib import Path


def slugify_filename(value: str) -> str:
    sanitized = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip()
    sanitized = re.sub(r"\s+", "-", sanitized)
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    return sanitized[:80].strip("-")


def normalize_text(value: str) -> str:
    normalized = html.unescape(value).replace("\xa0", " ").strip()
    return " ".join(normalized.split())


def build_public_file_name(file_name: str, fallback_stem: str = "video") -> str:
    path = Path(file_name)
    suffix = path.suffix.lower()
    stem = path.stem

    normalized_stem = normalize_text(stem)
    normalized_stem = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "-", normalized_stem)
    normalized_stem = re.sub(r"-{2,}", "-", normalized_stem).strip("-")

    if not normalized_stem:
        normalized_stem = fallback_stem

    max_stem_length = max(1, 120 - len(suffix))
    return f"{normalized_stem[:max_stem_length]}{suffix}"
