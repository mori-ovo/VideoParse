from pydantic import AnyHttpUrl, BaseModel, Field

from app.schemas.task import DeliveryMode, TaskRecord


class ParseRequest(BaseModel):
    url: AnyHttpUrl = Field(..., description="需要解析的视频页面链接。")
    delivery_mode: DeliveryMode = Field(
        default=DeliveryMode.DIRECT,
        description="默认优先提取直链，只有显式要求时才下载并合成文件。",
    )


class ParseAcceptedResponse(BaseModel):
    task: TaskRecord
    note: str = Field(..., description="基础骨架阶段的说明信息。")
