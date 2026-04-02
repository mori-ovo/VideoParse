from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.core.config import settings
from app.services.cleanup_service import cleanup_service
from app.services.proxy_service import proxy_service


def ensure_runtime_directories() -> None:
    for directory in settings.runtime_directories:
        directory.mkdir(parents=True, exist_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_runtime_directories()
    await cleanup_service.start()
    await proxy_service.start()
    app.state.cleanup_service = cleanup_service
    app.state.proxy_service = proxy_service
    yield
    await cleanup_service.stop()
    await proxy_service.stop()
