from fastapi import APIRouter

from app.api.v1.endpoints import files, health, history, parse, tasks, telegram

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(parse.router)
api_router.include_router(tasks.router)
api_router.include_router(files.router)
api_router.include_router(history.router)
api_router.include_router(telegram.router)
