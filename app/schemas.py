from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(default="default", description="对话会话 ID")
    message: str = Field(..., min_length=1, description="用户输入")
