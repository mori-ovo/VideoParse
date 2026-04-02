import re


def slugify_filename(value: str) -> str:
    sanitized = re.sub(r"[\\/:*?\"<>|]+", "-", value).strip()
    sanitized = re.sub(r"\s+", "-", sanitized)
    sanitized = re.sub(r"-{2,}", "-", sanitized)
    return sanitized[:80].strip("-")
