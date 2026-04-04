from typing import Any

from fastapi import APIRouter, Header, HTTPException, status

from app.core.config import settings
from app.services.telegram_service import TelegramServiceError, telegram_service

router = APIRouter(prefix="/telegram", tags=["telegram"])


@router.post("/webhook", summary="接收 Telegram webhook 更新")
async def receive_telegram_webhook(
    payload: dict[str, Any],
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict[str, bool]:
    if settings.telegram_update_mode != "webhook":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Telegram webhook mode is not enabled.",
        )

    try:
        await telegram_service.handle_webhook_update(
            payload,
            secret_token=x_telegram_bot_api_secret_token,
        )
    except TelegramServiceError as exc:
        detail = str(exc).strip() or "telegram webhook request failed"
        if "secret" in detail.lower():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=detail,
            ) from exc
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        ) from exc

    return {"ok": True}
