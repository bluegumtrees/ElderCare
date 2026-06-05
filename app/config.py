from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openrouter_api_key: str
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    chat_model: str = "deepseek/deepseek-chat"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    chroma_dir: str = "./chroma_data"
    sqlite_path: str = "./data/app.db"

    # RAG 检索参数
    reranker_model: str = "BAAI/bge-reranker-base"
    retrieve_candidate_k: int = 20  # 向量召回数量（宽召回）
    retrieve_top_n: int = 3  # rerank 后保留数量（精排后送 LLM）

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
