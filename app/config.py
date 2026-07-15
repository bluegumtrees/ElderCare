from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 缺省空串而不是必填：构建期跑 ingest/BM25 预热不需要 key，
    # 运行期缺 key 时 LLM 调用会失败并被前端友好兜底，启动时打警告
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # 生成模型：Qwen 旗舰 plus，中文对话语气自然；可用 env 换回 deepseek/deepseek-chat
    chat_model: str = "qwen/qwen3.7-plus"
    # 意图分类模型：小而快，降低首字延迟；分类是 JSON 单轮任务，flash 档足够
    intent_model: str = "qwen/qwen3.5-flash-02-23"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    chroma_dir: str = "./chroma_data"
    sqlite_path: str = "./data/app.db"

    # RAG 检索参数
    reranker_model: str = "BAAI/bge-reranker-base"
    retrieve_candidate_k: int = 20  # 向量召回数量（宽召回）
    retrieve_top_n: int = 3  # rerank 后保留数量（精排后送 LLM）

    # 管理端鉴权：为空时不校验（本地开发），公网部署务必设置
    admin_token: str = ""

    # 登录会话
    auth_token_ttl_days: int = 30
    # demo 账号（启动时自动创建并预置演示对话）
    demo_username: str = "demo"
    demo_password: str = "demo2026"

    # SMTP（全部可选；任一为空则 notifier 走 stdout dev 模式）
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    alert_to_email: str = ""

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and self.alert_to_email)


@lru_cache
def get_settings() -> Settings:
    return Settings()
