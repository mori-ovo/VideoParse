import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.config import settings
from app.services.storage_service import storage_service
from app.services.telegram_service import telegram_service

logger = logging.getLogger(__name__)


class CleanupService:
    def __init__(self) -> None:
        self._runner_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return

        self._stop_event = asyncio.Event()
        self._runner_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._runner_task is None:
            return

        self._stop_event.set()
        await self._runner_task
        self._runner_task = None

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self.run_cleanup_cycle()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=settings.cleanup_interval_seconds,
                )
            except TimeoutError:
                continue

    async def run_cleanup_cycle(self) -> dict[str, int]:
        threshold = datetime.now(timezone.utc) - timedelta(hours=settings.cleanup_retention_hours)
        deleted_files = 0
        deleted_dirs = 0

        # temp、cache、output 三个目录统一按最近修改时间清理。
        for directory in (settings.temp_dir, settings.cache_dir, settings.output_dir):
            deleted_files += self._remove_expired_files(directory, threshold)
            deleted_dirs += self._remove_empty_dirs(directory)

        # 本地文件和 Telegram 短链索引都要同步修正，避免保留失效记录。
        pruned_index_entries = await storage_service.prune_missing_files()
        pruned_telegram_entries = await telegram_service.prune_expired_entries(threshold)

        logger.info(
            "cleanup finished: deleted_files=%s deleted_dirs=%s pruned_index_entries=%s pruned_telegram_entries=%s interval_hours=%s retention_hours=%s",
            deleted_files,
            deleted_dirs,
            pruned_index_entries,
            pruned_telegram_entries,
            settings.cleanup_interval_hours,
            settings.cleanup_retention_hours,
        )
        return {
            "deleted_files": deleted_files,
            "deleted_dirs": deleted_dirs,
            "pruned_index_entries": pruned_index_entries,
            "pruned_telegram_entries": pruned_telegram_entries,
        }

    def _remove_expired_files(self, base_dir: Path, threshold: datetime) -> int:
        if not base_dir.exists():
            return 0

        deleted = 0
        for path in base_dir.rglob("*"):
            if not path.is_file():
                continue
            if path.name.startswith("."):
                continue
            modified_at = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
            if modified_at >= threshold:
                continue
            try:
                # Windows 下文件可能还被占用，清理任务不应该因为单个文件失败而中断。
                path.unlink(missing_ok=True)
                deleted += 1
            except OSError:
                continue
        return deleted

    def _remove_empty_dirs(self, base_dir: Path) -> int:
        if not base_dir.exists():
            return 0

        deleted = 0
        directories = sorted(
            [path for path in base_dir.rglob("*") if path.is_dir()],
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            try:
                directory.rmdir()
                deleted += 1
            except OSError:
                continue
        return deleted


cleanup_service = CleanupService()
