from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[4] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://study_app@127.0.0.1:5432/study_platform"
    graph_database_url: str = "postgresql://study_graph@127.0.0.1:5432/study_platform"
    database_pool_size: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
