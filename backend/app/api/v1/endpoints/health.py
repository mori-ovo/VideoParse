from fastapi import APIRouter

from app.core.config import settings
from app.services.downloader_service import downloader_service
from app.services.telegram_service import telegram_service

router = APIRouter(prefix="/health", tags=["health"])


@router.get("", summary="健康检查")
async def health_check() -> dict[str, object]:
    availability = downloader_service.availability()
    return {
        "status": "ok",
        "app_name": settings.app_name,
        "cleanup_interval_hours": settings.cleanup_interval_hours,
        "cleanup_retention_hours": settings.cleanup_retention_hours,
        "api_public_origin": settings.api_public_origin,
        "yt_dlp_available": availability.yt_dlp_available,
        "ffmpeg_available": availability.ffmpeg_available,
        "media_access_refresh_interval_seconds": settings.media_access_refresh_interval_seconds,
        "internal_media_redirect_enabled": bool(settings.internal_media_redirect_header),
        "default_delivery_mode": "auto",
        "supported_platforms": [
            "bilibili",
            "douyin",
            "twitter",
            "youtube",
            "reddit",
            "iwara",
            "telegram-bot",
        ],
        "telegram": telegram_service.status(),
    }
