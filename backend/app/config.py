from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./fleet.db"
    redis_url: str = "redis://localhost:6379/0"
    environment: str = "development"
    contract_version: str = "1.0"
    clock_mode: str = "SIMULATION"
    simulation_time: str = "2026-10-26T10:00:00+08:00"
    login_signing_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    task_token_signing_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    tool_service_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    access_token_seconds: int = 3600
    task_token_seconds: int = 300
    snapshot_ttl_seconds: int = 300
    candidate_ttl_seconds: int = 300
    reservation_ttl_seconds: int = 60
    idempotency_ttl_seconds: int = 86400
    computation_budget_seconds: int = 60
    agent_mode: Literal["local", "deepseek"] = "local"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    deepseek_api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    cors_allow_origins: list[str] = Field(default_factory=list)
    amap_web_service_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    amap_request_timeout_seconds: float = Field(default=10, ge=1, le=30)
    amap_cache_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    amap_total_budget_seconds: float = Field(default=60, ge=1, le=60)

    @field_validator("cors_allow_origins")
    @classmethod
    def explicit_origins(cls, values):
        for value in values:
            parsed = urlparse(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path
                or "*" in value
            ):
                raise ValueError("CORS origins must be explicit HTTP(S) origins without paths")
        return values

    platform_wait_seconds: int = 45
    tool_read_seconds: int = 10
    connection_timeout_seconds: int = 3
    maintenance_interval_seconds: int = 2
    auto_approval_enabled: bool = False
    auto_approval_max_changed_orders: int = Field(default=2, ge=1, le=20)
    auto_approval_max_delay_minutes: int = Field(default=30, ge=0, le=1440)


def get_settings() -> Settings:
    return Settings()
