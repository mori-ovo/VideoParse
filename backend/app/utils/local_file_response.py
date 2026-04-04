from pathlib import Path
from urllib.parse import quote

from fastapi.responses import FileResponse, Response

from app.core.config import settings


def build_local_file_response(
    *,
    path: Path,
    media_type: str,
    file_name: str | None,
    as_attachment: bool,
) -> Response:
    resolved_path = path.resolve()
    redirect_headers = _build_internal_redirect_headers(
        path=resolved_path,
        media_type=media_type,
        file_name=file_name,
        as_attachment=as_attachment,
    )
    if redirect_headers is not None:
        return Response(content=b"", status_code=200, headers=redirect_headers)

    response_kwargs = {
        "path": resolved_path,
        "media_type": media_type,
        "content_disposition_type": "attachment" if as_attachment else "inline",
    }
    if file_name is not None:
        response_kwargs["filename"] = file_name
    return FileResponse(**response_kwargs)


def _build_internal_redirect_headers(
    *,
    path: Path,
    media_type: str,
    file_name: str | None,
    as_attachment: bool,
) -> dict[str, str] | None:
    header_name = settings.internal_media_redirect_header
    if not isinstance(header_name, str) or not header_name:
        return None

    if header_name.lower() == "x-sendfile":
        header_value = str(path)
    else:
        header_value = _build_internal_redirect_path(path)
        if header_value is None:
            return None

    headers = {
        header_name: header_value,
        "Content-Type": media_type,
        "Accept-Ranges": "bytes",
    }
    if file_name is not None:
        disposition = "attachment" if as_attachment else "inline"
        headers["Content-Disposition"] = f"{disposition}; filename*=UTF-8''{quote(file_name)}"
    return headers


def _build_internal_redirect_path(path: Path) -> str | None:
    root = settings.internal_media_redirect_root
    prefix = settings.internal_media_redirect_prefix
    if not isinstance(root, str) or not root or not isinstance(prefix, str) or not prefix:
        return None

    try:
        relative_path = path.relative_to(Path(root).expanduser().resolve())
    except ValueError:
        return None

    normalized_prefix = prefix if prefix.startswith("/") else f"/{prefix}"
    normalized_prefix = normalized_prefix.rstrip("/")
    quoted_relative_path = "/".join(quote(part) for part in relative_path.parts)
    if not quoted_relative_path:
        return normalized_prefix or "/"
    return f"{normalized_prefix}/{quoted_relative_path}"
