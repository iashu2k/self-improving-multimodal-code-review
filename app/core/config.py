from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, PostgresDsn, RedisDsn, model_validator
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
  config_version: str = "v1.0"

  secret_key: str = Field(min_length=32)
  feedback_hash_secret: str | None = Field(default=None, min_length=32)

  openrouter_api_key: str | None = None
  openrouter_base_url: str = "https://openrouter.ai/api/v1"
  openrouter_site_url: str = "http://localhost:8000"
  openrouter_app_name: str = "self-improving-multimodal-code-review"
  openrouter_daily_cost_cap_usd: float = 30.0  # OPENROUTER_DAILY_COST_CAP_USD

  openrouter_review_model: str | None = None
  openrouter_critic_model: str | None = None
  openrouter_router_model: str | None = None
  openrouter_vision_model: str | None = None
  openrouter_judge_model: str | None = None
  openrouter_embedding_model: str | None = None

  database_url: PostgresDsn | None = None
  redis_url: RedisDsn | None = None

  github_app_id: int | None = None
  github_private_key_path: str | None = None
  github_webhook_secret: str | None = None
  # read-only PAT for public repo data; never used by the app
  github_dataset_token: str = ""

  langfuse_enabled: bool = False
  langfuse_public_key: str | None = None
  langfuse_secret_key: str | None = None
  langfuse_host: str = "https://cloud.langfuse.com"
  langfuse_project_id: str | None = None

  review_max_files: int = Field(default=30, alias="REVIEW_MAX_FILES")
  review_max_added_lines: int = Field(default=1500, alias="REVIEW_MAX_ADDED_LINES")

  # --- Phase 5: multimodal UI review ---
  # master switch; off until live-verified
  vision_enabled: bool = False
  screenshot_dir: Path = Path("data/screenshots")
  # metadata persisted; image retention configurable
  screenshot_retention_days: int = 7
  preview_timeout_seconds: int = 240
  preview_mem_limit: str = "1g"
  preview_nano_cpus: int = 1_000_000_000  # 1 CPU

  @model_validator(mode="after")
  def validate_feedback_hash_secret(self) -> "Settings":
    if self.app_env == "production" and not self.feedback_hash_secret:
      raise ValueError("FEEDBACK_HASH_SECRET must be configured in production")
    return self

  @property
  def feedback_actor_hash_secret(self) -> str:
    return self.feedback_hash_secret or self.secret_key


@lru_cache
def get_settings() -> Settings:
  return Settings()


settings = get_settings()
