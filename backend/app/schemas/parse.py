from pydantic import BaseModel, Field, field_validator

from app.schemas.task import DeliveryMode, TaskRecord


class ParseRequest(BaseModel):
    url: str = Field(..., description="需要解析的视频页面链接，或可识别的平台编号。")
    delivery_mode: DeliveryMode = Field(
        default=DeliveryMode.AUTO,
        description="默认自动模式：能直接拿单文件直链就直接返回，否则自动下载并合流。",
    )

    @field_validator("url", mode="before")
    @classmethod
    def normalize_url_text(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("url 不能为空")
        return normalized


class ParseAcceptedResponse(BaseModel):
    task: TaskRecord
    note: str = Field(..., description="解析任务已创建。")
