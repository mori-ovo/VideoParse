from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.services.access_log_service import media_access_log_service
from app.services.cleanup_service import cleanup_service
from app.services.proxy_service import proxy_service
from app.services.server_log_service import server_log_service
from app.services.task_service import task_service
from app.services.telegram_service import telegram_service


def ensure_runtime_directories() -> None:
    for directory in settings.runtime_directories:
        directory.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_directories()
    await server_log_service.start()
    await media_access_log_service.start()
    await task_service.recover_tasks()
    await cleanup_service.start()
    await proxy_service.start()
    await telegram_service.start()
    app.state.server_log_service = server_log_service
    app.state.media_access_log_service = media_access_log_service
    app.state.cleanup_service = cleanup_service
    app.state.proxy_service = proxy_service
    app.state.task_service = task_service
    app.state.telegram_service = telegram_service
    yield
    await server_log_service.stop()
    await media_access_log_service.stop()
    await cleanup_service.stop()
    await telegram_service.stop()
    await task_service.stop()
    await proxy_service.stop()
