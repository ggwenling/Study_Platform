from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[4] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="postgresql+psycopg://study_app@127.0.0.1:5432/study_platform", repr=False
    )
    graph_database_url: str = Field(
        default="postgresql://study_graph@127.0.0.1:5432/study_platform", repr=False
    )
    database_pool_size: int = 5
    storage_dir: Path = Path(__file__).resolve().parents[4] / ".local" / "storage"
    session_ttl_seconds: int = 604800
    cookie_secure: bool = False
    login_detailed_errors: bool = False
    auth_jwt_secret: SecretStr = SecretStr("")
    auth_public_url: str = "http://127.0.0.1:5173"
    google_client_id: str = ""
    google_client_secret: SecretStr = SecretStr("")
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_starttls: bool = True
    smtp_user: str = ""
    smtp_password: SecretStr = SecretStr("")
    smtp_from: str = ""
    allowed_origins: list[str] = [
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:8000", "http://127.0.0.1:8000",
        "http://testserver",
    ]
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_api_key: SecretStr = SecretStr("")
    chat_model: str = "deepseek-flash"
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "qwen3.7-text-embedding-flash"
    embedding_dimensions: Literal[1024] = 1024
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 50
    max_image_pixels: int = 20_000_000
    worker_poll_seconds: float = 1
    job_lease_seconds: int = 300
    chat_lease_seconds: int = 90
    model_timeout_seconds: float = 60
    model_max_output_tokens: int = 2048
    retrieval_top_k: int = 6
    retrieval_min_similarity: float = 0.35


@lru_cache
def get_settings() -> Settings:
    return Settings()
