import asyncio
import logging
import os
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
            except asyncio.TimeoutError:
                continue

    async def run_cleanup_cycle(self) -> dict[str, int]:
        threshold = datetime.now(timezone.utc) - timedelta(hours=settings.cleanup_retention_hours)
        deleted_files = 0
        deleted_dirs = 0

        for directory in (settings.temp_dir, settings.cache_dir, settings.output_dir):
            removed_files, removed_dirs = self._cleanup_directory(directory, threshold)
            deleted_files += removed_files
            deleted_dirs += removed_dirs

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

    def _cleanup_directory(self, base_dir: Path, threshold: datetime) -> tuple[int, int]:
        if not base_dir.exists():
            return 0, 0

        deleted_files = 0
        deleted_dirs = 0
        for root, dirnames, filenames in os.walk(base_dir, topdown=False):
            root_path = Path(root)
            for file_name in filenames:
                if file_name.startswith("."):
                    continue
                file_path = root_path / file_name
                try:
                    modified_at = datetime.fromtimestamp(file_path.stat().st_mtime, timezone.utc)
                except OSError:
                    continue
                if modified_at >= threshold:
                    continue
                try:
                    file_path.unlink(missing_ok=True)
                    deleted_files += 1
                except OSError:
                    continue
            for dirname in dirnames:
                directory = root_path / dirname
                try:
                    directory.rmdir()
                    deleted_dirs += 1
                except OSError:
                    continue
        return deleted_files, deleted_dirs


cleanup_service = CleanupService()
