from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Platform(str, Enum):
    BILIBILI = "bilibili"
    DOUYIN = "douyin"
    TWITTER = "twitter"
    YOUTUBE = "youtube"
    REDDIT = "reddit"


class DeliveryMode(str, Enum):
    DIRECT = "direct"
    DOWNLOAD = "download"


class TaskStatus(str, Enum):
    PENDING = "pending"
    PARSING = "parsing"
    DOWNLOADING = "downloading"
    MERGING = "merging"
    UPLOADING = "uploading"
    SUCCESS = "success"
    FAILED = "failed"


class ResultType(str, Enum):
    DIRECT = "direct"
    DOWNLOAD = "download"
    SPLIT_STREAMS = "split_streams"


class TaskResult(BaseModel):
    result_type: ResultType
    file_id: str | None = None
    file_name: str | None = None
    content_type: str | None = None
    download_url: str | None = None
    direct_url: str | None = None
    redirect_url: str | None = None
    proxy_url: str | None = None
    video_url: str | None = None
    video_redirect_url: str | None = None
    video_proxy_url: str | None = None
    audio_url: str | None = None
    audio_redirect_url: str | None = None
    audio_proxy_url: str | None = None
    placeholder: bool = False
    created_at: datetime
    file_size: int | None = None
    expires_note: str | None = None


class TaskRecord(BaseModel):
    task_id: str
    source_url: str
    platform: Platform
    delivery_mode: DeliveryMode = DeliveryMode.DIRECT
    status: TaskStatus
    progress: int = Field(default=0, ge=0, le=100)
    title: str
    message: str
    requires_merge: bool = False
    direct_playable: bool = False
    created_at: datetime
    updated_at: datetime
    result: TaskResult | None = None
    error_message: str | None = None
    uploader: str | None = None
    duration: int | None = None
    thumbnail: str | None = None
    extractor: str | None = None
