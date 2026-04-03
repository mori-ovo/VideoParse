from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
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

    proxy: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PROXY", "YT_DLP_PROXY"),
    )
    cookies: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COOKIES", "YT_DLP_COOKIES"),
    )
    cookies_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("COOKIES_FILE", "YT_DLP_COOKIES_FILE"),
    )
    user_agent: str | None = Field(
        default=None,
        validation_alias=AliasChoices("USER_AGENT", "YT_DLP_USER_AGENT"),
    )

    bilibili_proxy: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_PROXY", "YT_DLP_BILIBILI_PROXY"),
    )
    bilibili_cookies: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_COOKIES", "YT_DLP_BILIBILI_COOKIES"),
    )
    bilibili_cookies_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_COOKIES_FILE", "YT_DLP_BILIBILI_COOKIES_FILE"),
    )
    bilibili_sessdata: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_SESSDATA"),
    )
    bilibili_bili_jct: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_BILI_JCT"),
    )
    bilibili_dedeuserid: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BILIBILI_DEDEUSERID", "BILIBILI_DEDE_USER_ID"),
    )

    youtube_cookies: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_COOKIES", "YT_DLP_YOUTUBE_COOKIES"),
    )
    youtube_cookies_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_COOKIES_FILE", "YT_DLP_YOUTUBE_COOKIES_FILE"),
    )
    youtube_player_client: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_PLAYER_CLIENT"),
    )
    youtube_po_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_PO_TOKEN"),
    )
    youtube_js_runtimes: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_JS_RUNTIMES"),
    )
    youtube_remote_components: str | None = Field(
        default=None,
        validation_alias=AliasChoices("YOUTUBE_REMOTE_COMPONENTS"),
    )

    twitter_cookies: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWITTER_COOKIES", "YT_DLP_TWITTER_COOKIES"),
    )
    twitter_cookies_file: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWITTER_COOKIES_FILE", "YT_DLP_TWITTER_COOKIES_FILE"),
    )
    twitter_auth_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWITTER_AUTH_TOKEN"),
    )
    twitter_ct0: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TWITTER_CT0"),
    )

    download_format: str = Field(
        default="bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best",
        validation_alias=AliasChoices("DOWNLOAD_FORMAT", "YT_DLP_DOWNLOAD_FORMAT"),
    )
    merge_output_format: str = Field(
        default="mp4",
        validation_alias=AliasChoices("MERGE_OUTPUT_FORMAT", "YT_DLP_MERGE_OUTPUT_FORMAT"),
    )
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
