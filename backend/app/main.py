from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import settings
from app.core.lifecycle import lifespan


def create_application() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            settings.frontend_origin,
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    @app.get("/", summary="服务入口")
    async def root() -> dict[str, object]:
        return {
            "message": "VideoParse API is running.",
            "health_url": f"{settings.api_v1_prefix}/health",
            "docs_url": "/docs",
            "frontend_dev_url": settings.frontend_origin,
        }

    return app


app = create_application()
