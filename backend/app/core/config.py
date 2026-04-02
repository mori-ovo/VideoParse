from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "VideoParse API"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    frontend_origin: str = "http://127.0.0.1:5173"
    api_public_origin: str = "http://127.0.0.1:8000"

    cleanup_interval_hours: int = 6
    cleanup_retention_hours: int = 6
    yt_dlp_download_format: str = "best[height<=1080]/bestvideo*[height<=1080]+bestaudio/best"
    yt_dlp_merge_output_format: str = "mp4"
    ffmpeg_location: str | None = None
    proxy_timeout_seconds: int = 30
    proxy_chunk_size: int = 65536
    proxy_max_connections: int = 20

    temp_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "temp")
    cache_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "cache")
    output_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "output")
    storage_index_path: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "output" / ".file-index.json"
    )

    @field_validator("debug", mode="before")
    @classmethod
    def normalize_debug(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"release", "prod", "production", "off", "0", "false", "no"}:
                return False
            if normalized in {"debug", "dev", "development", "on", "1", "true", "yes"}:
                return True
        return value

    @property
    def cleanup_interval_seconds(self) -> int:
        return self.cleanup_interval_hours * 60 * 60

    @property
    def runtime_directories(self) -> tuple[Path, Path, Path]:
        return (self.temp_dir, self.cache_dir, self.output_dir)


settings = Settings()
