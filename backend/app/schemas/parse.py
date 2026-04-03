from pydantic import AnyHttpUrl, BaseModel, Field

from app.schemas.task import DeliveryMode, TaskRecord


class ParseRequest(BaseModel):
    url: AnyHttpUrl = Field(..., description="需要解析的视频页面链接。")
    delivery_mode: DeliveryMode = Field(
        default=DeliveryMode.AUTO,
        description="默认自动模式：能直接拿单文件直链就直接返回，否则自动下载并合流。",
    )


class ParseAcceptedResponse(BaseModel):
    task: TaskRecord
    note: str = Field(..., description="解析任务已创建。")
