import asyncio
import logging
import threading
from dataclasses import dataclass, field
from time import monotonic

from app.core.config import settings

logger = logging.getLogger("uvicorn.error")


@dataclass
class AggregatedAccessEntry:
    method: str
    path: str
    count: int = 0
    first_seen_at: float = field(default_factory=monotonic)
    last_seen_at: float = field(default_factory=monotonic)
    clients: set[str] = field(default_factory=set)
    status_counts: dict[int, int] = field(default_factory=dict)


class MediaAccessLogFilter(logging.Filter):
    def __init__(self, service: "MediaAccessLogService") -> None:
        super().__init__()
        self._service = service

    def filter(self, record: logging.LogRecord) -> bool:
        return self._service.handle_access_record(record)


class MediaAccessLogService:
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], AggregatedAccessEntry] = {}
        self._lock = threading.Lock()
        self._flush_task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._filter = MediaAccessLogFilter(self)
        self._started = False

    async def start(self) -> None:
        if not settings.media_access_log_aggregation_enabled:
            return
        if self._started:
            return

        access_logger = logging.getLogger("uvicorn.access")
        access_logger.addFilter(self._filter)
        self._stop_event = asyncio.Event()
        self._flush_task = asyncio.create_task(self._flush_loop())
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return

        self._stop_event.set()
        if self._flush_task is not None:
            await asyncio.gather(self._flush_task, return_exceptions=True)
            self._flush_task = None

        logging.getLogger("uvicorn.access").removeFilter(self._filter)
        self._flush(force=True)
        self._started = False

    def handle_access_record(self, record: logging.LogRecord) -> bool:
        if getattr(record, "_skip_media_access_aggregation", False):
            return True

        parsed = self._parse_access_record(record)
        if parsed is None:
            return True

        client_addr, method, path, status_code = parsed
        if method not in {"GET", "HEAD"}:
            return True
        if not path.startswith(f"{settings.api_v1_prefix}/files/"):
            return True

        now = monotonic()
        key = (method, path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                entry = AggregatedAccessEntry(
                    method=method,
                    path=path,
                    count=1,
                    first_seen_at=now,
                    last_seen_at=now,
                    status_counts={status_code: 1},
                )
                self._entries[key] = entry
            else:
                entry.count += 1
                entry.last_seen_at = now
                entry.status_counts[status_code] = entry.status_counts.get(status_code, 0) + 1
            if client_addr:
                entry.clients.add(client_addr)

        # 返回 False，拦截原始 access log，避免分段请求刷屏。
        return False

    async def _flush_loop(self) -> None:
        interval = max(1, settings.media_access_log_window_seconds)
        try:
            while not self._stop_event.is_set():
                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
                except asyncio.TimeoutError:
                    pass
                self._flush(force=False)
        except asyncio.CancelledError:
            return

    def _flush(self, *, force: bool) -> None:
        now = monotonic()
        window = max(1, settings.media_access_log_window_seconds)

        ready_entries: list[AggregatedAccessEntry] = []
        with self._lock:
            expired_keys: list[tuple[str, str]] = []
            for key, entry in self._entries.items():
                if force or now - entry.last_seen_at >= window:
                    expired_keys.append(key)
                    ready_entries.append(entry)
            for key in expired_keys:
                self._entries.pop(key, None)

        for entry in sorted(ready_entries, key=lambda item: (item.path, item.method)):
            duration = max(0.1, entry.last_seen_at - entry.first_seen_at)
            status_summary = ",".join(
                f"{status}x{count}" for status, count in sorted(entry.status_counts.items())
            )
            logger.info(
                "媒体访问聚合 method=%s count=%s statuses=%s clients=%s window=%.1fs path=%s",
                entry.method,
                entry.count,
                status_summary,
                len(entry.clients),
                duration,
                entry.path,
            )

    def _parse_access_record(self, record: logging.LogRecord) -> tuple[str, str, str, int] | None:
        args = record.args
        if not isinstance(args, tuple) or len(args) < 5:
            return None

        client_addr = str(args[0] or "")
        method = str(args[1] or "").upper()
        path = str(args[2] or "")
        try:
            status_code = int(args[4])
        except (TypeError, ValueError):
            return None
        return client_addr, method, path, status_code


media_access_log_service = MediaAccessLogService()
