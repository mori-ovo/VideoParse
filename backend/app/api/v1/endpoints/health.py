from fastapi import APIRouter

from app.core.config import settings
from app.services.downloader_service import downloader_service

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
        "default_delivery_mode": "direct",
        "supported_platforms": [
            "bilibili",
            "douyin",
            "twitter",
            "youtube",
            "reddit",
        ],
    }
