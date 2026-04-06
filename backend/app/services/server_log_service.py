import logging


class _SuppressExactMessageFilter(logging.Filter):
    def __init__(self, suppressed_messages: set[str]) -> None:
        super().__init__()
        self._suppressed_messages = suppressed_messages

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        return message not in self._suppressed_messages


class ServerLogService:
    def __init__(self) -> None:
        self._uvicorn_error_logger = logging.getLogger("uvicorn.error")
        self._filter = _SuppressExactMessageFilter(
            suppressed_messages={"Invalid HTTP request received."}
        )
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._uvicorn_error_logger.addFilter(self._filter)
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        self._uvicorn_error_logger.removeFilter(self._filter)
        self._started = False


server_log_service = ServerLogService()
