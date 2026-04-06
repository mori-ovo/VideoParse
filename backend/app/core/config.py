import hashlib
from pathlib import Path

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def env_alias_choices(*names: str) -> AliasChoices:
    expanded_names: list[str] = []
    seen: set[str] = set()
    for name in names:
        for candidate in (name.lower(), name):
            if candidate in seen:
                continue
            seen.add(candidate)
            expanded_names.append(candidate)
    return AliasChoices(*expanded_names)


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

    # 临时文件、缓存文件和输出文件的清理周期与保留时间。
    cleanup_interval_hours: int = 4
    cleanup_retention_hours: int = 4

    proxy: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("PROXY", "YT_DLP_PROXY"),
    )
    cookies: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("COOKIES", "YT_DLP_COOKIES"),
    )
    cookies_file: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("COOKIES_FILE", "YT_DLP_COOKIES_FILE"),
    )
    user_agent: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("USER_AGENT", "YT_DLP_USER_AGENT"),
    )

    bilibili_proxy: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_PROXY", "YT_DLP_BILIBILI_PROXY"),
    )
    bilibili_cookies: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_COOKIES", "YT_DLP_BILIBILI_COOKIES"),
    )
    bilibili_cookies_file: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_COOKIES_FILE", "YT_DLP_BILIBILI_COOKIES_FILE"),
    )
    bilibili_sessdata: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_SESSDATA"),
    )
    bilibili_bili_jct: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_BILI_JCT"),
    )
    bilibili_dedeuserid: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("BILIBILI_DEDEUSERID", "BILIBILI_DEDE_USER_ID"),
    )

    youtube_cookies: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_COOKIES", "YT_DLP_YOUTUBE_COOKIES"),
    )
    youtube_cookies_file: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_COOKIES_FILE", "YT_DLP_YOUTUBE_COOKIES_FILE"),
    )
    youtube_player_client: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_PLAYER_CLIENT"),
    )
    youtube_po_token: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_PO_TOKEN"),
    )
    youtube_js_runtimes: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_JS_RUNTIMES"),
    )
    youtube_remote_components: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("YOUTUBE_REMOTE_COMPONENTS"),
    )

    twitter_cookies: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TWITTER_COOKIES", "YT_DLP_TWITTER_COOKIES"),
    )
    twitter_cookies_file: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TWITTER_COOKIES_FILE", "YT_DLP_TWITTER_COOKIES_FILE"),
    )
    twitter_auth_token: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TWITTER_AUTH_TOKEN"),
    )
    twitter_ct0: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TWITTER_CT0"),
    )

    iwara_authorization: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("IWARA_AUTHORIZATION", "IWARA_BEARER_TOKEN"),
    )
    iwara_cookies: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("IWARA_COOKIES"),
    )
    iwara_user_agent: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("IWARA_USER_AGENT"),
    )

    douyin_fallback_enabled: bool = Field(
        default=True,
        validation_alias=env_alias_choices("DOUYIN_FALLBACK_ENABLED"),
    )
    douyin_fallback_api_base: str = Field(
        default="https://tools.devresourcehub.com",
        validation_alias=env_alias_choices("DOUYIN_FALLBACK_API_BASE"),
    )
    douyin_fallback_analyze_path: str = Field(
        default="/api/v1/douyin",
        validation_alias=env_alias_choices("DOUYIN_FALLBACK_ANALYZE_PATH"),
    )
    douyin_fallback_timeout_seconds: int = Field(
        default=20,
        validation_alias=env_alias_choices("DOUYIN_FALLBACK_TIMEOUT_SECONDS"),
    )

    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
    )
    telegram_bot_api_base: str = Field(
        default="http://127.0.0.1:8081",
        validation_alias=env_alias_choices("TELEGRAM_BOT_API_BASE", "TG_BOT_API_BASE"),
    )
    telegram_polling_enabled: bool = Field(
        default=True,
        validation_alias=env_alias_choices("TELEGRAM_POLLING_ENABLED", "TG_POLLING_ENABLED"),
    )
    telegram_update_mode: str = Field(
        default="polling",
        validation_alias=env_alias_choices("TELEGRAM_UPDATE_MODE", "TG_UPDATE_MODE"),
    )
    telegram_webhook_url: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TELEGRAM_WEBHOOK_URL", "TG_WEBHOOK_URL"),
    )
    telegram_webhook_secret: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TELEGRAM_WEBHOOK_SECRET", "TG_WEBHOOK_SECRET"),
    )
    telegram_poll_timeout_seconds: int = Field(
        default=20,
        validation_alias=env_alias_choices("TELEGRAM_POLL_TIMEOUT_SECONDS", "TG_POLL_TIMEOUT_SECONDS"),
    )
    telegram_poll_interval_seconds: int = Field(
        default=2,
        validation_alias=env_alias_choices("TELEGRAM_POLL_INTERVAL_SECONDS", "TG_POLL_INTERVAL_SECONDS"),
    )
    telegram_file_timeout_seconds: int = Field(
        default=600,
        validation_alias=env_alias_choices("TELEGRAM_FILE_TIMEOUT_SECONDS", "TG_FILE_TIMEOUT_SECONDS"),
    )
    telegram_sync_cache_max_mb: int = Field(
        default=300,
        validation_alias=env_alias_choices("TELEGRAM_SYNC_CACHE_MAX_MB", "TG_SYNC_CACHE_MAX_MB"),
    )
    telegram_file_prefetch_enabled: bool = Field(
        default=False,
        validation_alias=env_alias_choices("TELEGRAM_FILE_PREFETCH_ENABLED", "TG_FILE_PREFETCH_ENABLED"),
    )
    telegram_local_file_source_prefix: str | None = Field(
        default=None,
        validation_alias=env_alias_choices(
            "TELEGRAM_LOCAL_FILE_SOURCE_PREFIX",
            "TG_LOCAL_FILE_SOURCE_PREFIX",
        ),
    )
    telegram_local_file_target_prefix: str | None = Field(
        default=None,
        validation_alias=env_alias_choices(
            "TELEGRAM_LOCAL_FILE_TARGET_PREFIX",
            "TG_LOCAL_FILE_TARGET_PREFIX",
        ),
    )
    telegram_allowed_chat_ids: str | None = Field(
        default=None,
        validation_alias=env_alias_choices("TELEGRAM_ALLOWED_CHAT_IDS", "TG_ALLOWED_CHAT_IDS"),
    )

    download_format: str = Field(
        default="bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best",
        validation_alias=env_alias_choices("DOWNLOAD_FORMAT", "YT_DLP_DOWNLOAD_FORMAT"),
    )
    # yt-dlp 对支持分片的源站启用并发分片下载，用来改善长视频吞吐。
    download_concurrent_fragment_downloads: int = Field(
        default=4,
        validation_alias=env_alias_choices(
            "DOWNLOAD_CONCURRENT_FRAGMENT_DOWNLOADS",
            "YT_DLP_CONCURRENT_FRAGMENT_DOWNLOADS",
        ),
    )
    merge_output_format: str = Field(
        default="mp4",
        validation_alias=env_alias_choices("MERGE_OUTPUT_FORMAT", "YT_DLP_MERGE_OUTPUT_FORMAT"),
    )
    ffmpeg_location: str | None = None
    proxy_timeout_seconds: int = 30
    proxy_chunk_size: int = 65536
    proxy_max_connections: int = 20
    media_access_refresh_interval_seconds: int = Field(
        default=300,
        validation_alias=env_alias_choices(
            "MEDIA_ACCESS_REFRESH_INTERVAL_SECONDS",
            "FILE_ACCESS_REFRESH_INTERVAL_SECONDS",
        ),
    )
    internal_media_redirect_header: str | None = Field(
        default=None,
        validation_alias=env_alias_choices(
            "INTERNAL_MEDIA_REDIRECT_HEADER",
            "MEDIA_INTERNAL_REDIRECT_HEADER",
        ),
    )
    internal_media_redirect_root: str | None = Field(
        default=None,
        validation_alias=env_alias_choices(
            "INTERNAL_MEDIA_REDIRECT_ROOT",
            "MEDIA_INTERNAL_REDIRECT_ROOT",
        ),
    )
    internal_media_redirect_prefix: str | None = Field(
        default=None,
        validation_alias=env_alias_choices(
            "INTERNAL_MEDIA_REDIRECT_PREFIX",
            "MEDIA_INTERNAL_REDIRECT_PREFIX",
        ),
    )
    # 元数据短缓存用于减少同一链接在解析、重定向、代理阶段的重复提取。
    metadata_cache_ttl_seconds: int = 300
    # 超过该时长且只有分离流时，auto 模式优先返回单链接合流代理。
    lazy_stream_min_duration_seconds: int = 600
    # 单链接合流代理允许 ffmpeg 预热的最长等待时间。
    lazy_stream_startup_timeout_seconds: int = 20

    # 聚合重复的媒体访问日志，减少播放器分段请求导致的日志刷屏。
    media_access_log_aggregation_enabled: bool = Field(
        default=True,
        validation_alias=env_alias_choices(
            "MEDIA_ACCESS_LOG_AGGREGATION_ENABLED",
            "FILE_ACCESS_LOG_AGGREGATION_ENABLED",
        ),
    )
    # 聚合窗口内的相同请求只输出一条摘要日志。
    media_access_log_window_seconds: int = Field(
        default=5,
        validation_alias=env_alias_choices(
            "MEDIA_ACCESS_LOG_WINDOW_SECONDS",
            "FILE_ACCESS_LOG_WINDOW_SECONDS",
        ),
    )

    temp_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "temp")
    cache_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "cache")
    output_dir: Path = Field(default_factory=lambda: PROJECT_ROOT / "output")
    storage_index_path: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "output" / ".file-index.json"
    )
    task_index_path: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "output" / ".task-index.json"
    )
    telegram_file_index_path: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "output" / ".telegram-file-index.json"
    )
    telegram_state_path: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "output" / ".telegram-bot-state.json"
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

    @field_validator("telegram_bot_api_base", mode="before")
    @classmethod
    def normalize_telegram_bot_api_base(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/")
        return value

    @field_validator("douyin_fallback_api_base", mode="before")
    @classmethod
    def normalize_douyin_fallback_api_base(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().rstrip("/")
        return value

    @field_validator("douyin_fallback_analyze_path", mode="before")
    @classmethod
    def normalize_douyin_fallback_analyze_path(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return "/api/v1/douyin"
            if not normalized.startswith("/"):
                return f"/{normalized}"
            return normalized
        return value

    @field_validator("telegram_update_mode", mode="before")
    @classmethod
    def normalize_telegram_update_mode(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().lower()
            if normalized in {"poll", "polling"}:
                return "polling"
            if normalized in {"hook", "webhook"}:
                return "webhook"
        return value

    @field_validator("telegram_webhook_url", "telegram_webhook_secret", mode="before")
    @classmethod
    def normalize_optional_telegram_text(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator("internal_media_redirect_header", mode="before")
    @classmethod
    def normalize_optional_redirect_header(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value

    @field_validator(
        "telegram_local_file_source_prefix",
        "telegram_local_file_target_prefix",
        "internal_media_redirect_root",
        "internal_media_redirect_prefix",
        mode="before",
    )
    @classmethod
    def normalize_optional_path_prefix(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip().rstrip("/")
            return normalized or None
        return value

    @property
    def cleanup_interval_seconds(self) -> int:
        return self.cleanup_interval_hours * 60 * 60

    @property
    def telegram_allowed_chat_id_set(self) -> set[int]:
        if not isinstance(self.telegram_allowed_chat_ids, str) or not self.telegram_allowed_chat_ids.strip():
            return set()

        chat_ids: set[int] = set()
        for raw_value in self.telegram_allowed_chat_ids.split(","):
            normalized = raw_value.strip()
            if not normalized:
                continue
            try:
                chat_ids.add(int(normalized))
            except ValueError:
                continue
        return chat_ids

    @property
    def telegram_bot_configured(self) -> bool:
        return isinstance(self.telegram_bot_token, str) and bool(self.telegram_bot_token.strip())

    @property
    def telegram_webhook_target_url(self) -> str:
        if isinstance(self.telegram_webhook_url, str) and self.telegram_webhook_url:
            return self.telegram_webhook_url
        return f"{self.api_public_origin}{self.api_v1_prefix}/telegram/webhook"

    @property
    def telegram_webhook_secret_value(self) -> str | None:
        if isinstance(self.telegram_webhook_secret, str) and self.telegram_webhook_secret:
            return self.telegram_webhook_secret
        if not self.telegram_bot_configured:
            return None
        digest = hashlib.sha256(self.telegram_bot_token.strip().encode("utf-8")).hexdigest()
        return digest[:32]

    @property
    def runtime_directories(self) -> tuple[Path, Path, Path]:
        return (self.temp_dir, self.cache_dir, self.output_dir)


settings = Settings()
