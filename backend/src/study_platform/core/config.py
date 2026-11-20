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
    model_connections_key: SecretStr = SecretStr("")
    model_allowed_origins: list[str] = []
    database_pool_size: int = 10
    storage_dir: Path = Path(__file__).resolve().parents[4] / ".local" / "storage"
    session_ttl_seconds: int = 604800
    cookie_secure: bool = False
    auth_rate_limit_per_minute: int = 30
    max_docx_uncompressed_bytes: int = 80 * 1024 * 1024
    max_pptx_ocr_images: int = 10
    max_web_clip_bytes: int = 5 * 1024 * 1024
    web_clip_timeout_seconds: float = 15
    max_pdf_page_preview: int = 50
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
    chat_fallback_model: str = ""
    chat_fallback_base_url: str = ""
    chat_fallback_api_key: SecretStr = SecretStr("")
    embedding_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    embedding_api_key: SecretStr = SecretStr("")
    embedding_model: str = "qwen3.7-text-embedding-flash"
    embedding_dimensions: Literal[1024] = 1024
    max_upload_bytes: int = 20 * 1024 * 1024
    max_pdf_pages: int = 50
    pdf_ocr_min_effective_chars: int = 40
    pdf_ocr_image_page_max_chars: int = 120
    max_vlm_pages_per_material: int = Field(default=20, ge=1)
    max_image_pixels: int = 20_000_000
    worker_poll_seconds: float = 1
    worker_heavy_concurrency: int = Field(default=1, ge=1, le=2)
    worker_generation_concurrency: int = Field(default=2, ge=1, le=4)
    job_lease_seconds: int = 300
    chat_lease_seconds: int = 90
    model_timeout_seconds: float = 60
    model_max_output_tokens: int = 2048
    retrieval_top_k: int = 10
    retrieval_candidate_k: int = 20
    retrieval_min_similarity: float = 0.35
    retrieval_hybrid_enabled: bool = True
    # Keyword branch weight in RRF. The branch works (OR + IDF), but on the hard
    # retrieval sets it was not a net win across embedding draws, so it is opt-in;
    # 0.4 is the best-tested value when enabling it.
    retrieval_lexical_weight: float = 0.0
    retrieval_lexical_scoring: str = "idf"
    rerank_timeout_seconds: float = 15.0
    rag_query_rewrite_enabled: bool = False
    learning_confidence_threshold: float = 0.85
    learning_stub_grader: bool = False
    learning_llm_timeout_seconds: float = 45
    learning_llm_max_output_tokens: int = 2048
    learning_llm_max_retries: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()
