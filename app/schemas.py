from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    session_id: str = Field(default="default", description="对话会话 ID")
    message: str = Field(..., min_length=1, description="用户输入")
    # 对比模式里裸 LLM 那一路不落库，避免同一条消息在历史里出现两遍
    save_history: bool = Field(default=True, description="是否把这轮对话写入历史")


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=20, pattern=r"^[a-zA-Z0-9_]+$")
    password: str = Field(..., min_length=6, max_length=64)
    display_name: str | None = Field(default=None, max_length=20)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=20)
    password: str = Field(..., min_length=1, max_length=64)
