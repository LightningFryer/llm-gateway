"""Application configuration settings for Relay."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server Settings
    relay_host: str = "0.0.0.0"
    relay_port: int = 8000
    relay_log_level: str = "INFO"
    relay_log_prompts: bool = False
    relay_admin_key: str = "relay_admin_secret_key_12345"

    # SQLite Database (Single file, no ORM, WAL mode)
    database_path: str = "relay.db"

    # Redis Connection URL
    redis_url: str = "redis://localhost:6379/0"

    # Model Alias Configuration File
    relay_config_path: str = "config/models.yaml"

    # Rate Limiting & Resilience
    relay_rate_limit_algorithm: Literal["token_bucket", "sliding_window"] = "token_bucket"
    relay_circuit_failure_threshold: float = 0.5
    relay_circuit_min_requests: int = 5
    relay_circuit_cooldown_seconds: float = 10.0
    relay_circuit_half_open_probes: int = 3

    # Response Caching
    relay_cache_enabled: bool = True
    relay_cache_default_ttl_seconds: int = 3600

    # Async Request Log Batching Queue
    relay_log_queue_maxsize: int = 10000
    relay_log_batch_size: int = 100
    relay_log_flush_interval_seconds: float = 0.5

    # Optional Real Upstream Provider Keys
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    gemini_api_key: str | None = None


@lru_cache
def get_settings() -> Settings:
    """Returns cached settings instance."""
    return Settings()
