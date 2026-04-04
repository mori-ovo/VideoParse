import asyncio
import json
import mimetypes
import secrets
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings
from app.schemas.task import ResultType, TaskRecord, TaskResult
from app.utils.path import build_public_file_name, slugify_filename


@dataclass
class StoredFile:
    file_id: str
    file_name: str
    path: Path
    content_type: str
    created_at: datetime
    file_size: int


class LocalStorageService:
    def __init__(self) -> None:
        self._files: dict[str, StoredFile] = self._load_index()
        self._lock = asyncio.Lock()

    async def save_placeholder_output(self, task: TaskRecord) -> TaskResult:
        safe_title = slugify_filename(task.title) or task.platform.value
        file_id = self._generate_file_id()
        file_name = f"{safe_title}-{file_id}.txt"
        file_path = settings.output_dir / file_name
        created_at = datetime.now(timezone.utc)

        content = "\n".join(
            [
                "VideoParse scaffold output",
                f"task_id={task.task_id}",
                f"platform={task.platform.value}",
                f"source_url={task.source_url}",
                f"requires_merge={task.requires_merge}",
                "status=success",
                "note=当前文件为项目骨架阶段生成的占位产物，后续会替换为真实下载视频文件。",
            ]
        )
        file_path.write_text(content, encoding="utf-8")

        stored_file = StoredFile(
            file_id=file_id,
            file_name=file_name,
            path=file_path,
            content_type="text/plain",
            created_at=created_at,
            file_size=file_path.stat().st_size,
        )
        async with self._lock:
            while file_id in self._files:
                file_id = self._generate_file_id()
                stored_file.file_id = file_id
            self._files[file_id] = stored_file
            self._persist_index()

        return TaskResult(
            result_type=ResultType.DOWNLOAD,
            file_id=file_id,
            file_name=file_name,
            content_type=stored_file.content_type,
            play_url=self.build_stream_url(file_id, file_name),
            download_url=f"{settings.api_public_origin}{settings.api_v1_prefix}/files/{file_id}/download",
            placeholder=True,
            created_at=created_at,
            file_size=stored_file.file_size,
        )

    async def register_downloaded_file(self, file_path: Path) -> TaskResult:
        file_id = self._generate_file_id()
        created_at = datetime.now(timezone.utc)
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        public_file_name = build_public_file_name(file_path.name)
        stored_file = StoredFile(
            file_id=file_id,
            file_name=public_file_name,
            path=file_path,
            content_type=content_type,
            created_at=created_at,
            file_size=file_path.stat().st_size,
        )

        async with self._lock:
            while file_id in self._files:
                file_id = self._generate_file_id()
                stored_file.file_id = file_id
            self._files[file_id] = stored_file
            self._persist_index()

        return TaskResult(
            result_type=ResultType.DOWNLOAD,
            file_id=file_id,
            file_name=stored_file.file_name,
            content_type=stored_file.content_type,
            play_url=self.build_stream_url(file_id, stored_file.file_name),
            download_url=f"{settings.api_public_origin}{settings.api_v1_prefix}/files/{file_id}/download",
            placeholder=False,
            created_at=created_at,
            file_size=stored_file.file_size,
        )

    async def get_file(self, file_id: str) -> StoredFile | None:
        async with self._lock:
            stored_file = self._files.get(file_id)
            if stored_file is None:
                return None
            if not stored_file.path.exists():
                del self._files[file_id]
                self._persist_index()
                return None
            # 用访问时间刷新 mtime，避免仍在使用的输出文件被清理任务删掉。
            stored_file.path.touch(exist_ok=True)
            return stored_file

    async def prune_missing_files(self) -> int:
        async with self._lock:
            # output 被清理后，索引里的悬空记录也要同步移除。
            missing_file_ids = [
                file_id
                for file_id, stored_file in self._files.items()
                if not stored_file.path.exists()
            ]
            for file_id in missing_file_ids:
                del self._files[file_id]

            if missing_file_ids:
                self._persist_index()
            return len(missing_file_ids)

    def _load_index(self) -> dict[str, StoredFile]:
        index_path = settings.storage_index_path
        if not index_path.exists():
            return {}

        raw_data = json.loads(index_path.read_text(encoding="utf-8"))
        files: dict[str, StoredFile] = {}
        for item in raw_data:
            path = Path(item["path"])
            files[item["file_id"]] = StoredFile(
                file_id=item["file_id"],
                file_name=item["file_name"],
                path=path,
                content_type=item["content_type"],
                created_at=datetime.fromisoformat(item["created_at"]),
                file_size=item["file_size"],
            )
        return files

    def _persist_index(self) -> None:
        index_path = settings.storage_index_path
        index_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            {
                "file_id": item.file_id,
                "file_name": item.file_name,
                "path": str(item.path),
                "content_type": item.content_type,
                "created_at": item.created_at.isoformat(),
                "file_size": item.file_size,
            }
            for item in self._files.values()
        ]
        index_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def build_stream_url(self, file_id: str, file_name: str) -> str:
        suffix = Path(file_name).suffix.lower()
        public_file_name = f"{file_id}{suffix}" if suffix else file_id
        safe_file_name = quote(public_file_name, safe="")
        return f"{settings.api_public_origin}{settings.api_v1_prefix}/files/{safe_file_name}"

    def _generate_file_id(self) -> str:
        alphabet = string.ascii_lowercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(12))


storage_service = LocalStorageService()
