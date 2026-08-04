from functools import lru_cache
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Self-Improving Multimodal Code Review"
    app_env: Literal["development", "test", "staging", "production"] = "development"
    debug: bool = False
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"

    secret_key: str = Field(min_length=32)

    openrouter_api_key: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_site_url: str = "http://localhost:8000"
    openrouter_app_name: str = "self-improving-multimodal-code-review"

    openrouter_review_model: str | None = None
    openrouter_critic_model: str | None = None
    openrouter_vision_model: str | None = None
    openrouter_judge_model: str | None = None

    database_url: PostgresDsn | None = None
    redis_url: RedisDsn | None = None

    github_app_id: str | None = None
    github_private_key: str | None = None
    github_webhook_secret: str | None = None

    langfuse_public_key: str | None = None
    langfuse_secret_key: str | None = None
    langfuse_host: str = "https://cloud.langfuse.com"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
