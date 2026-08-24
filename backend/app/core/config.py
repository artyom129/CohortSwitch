from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="COHORTSWITCH_",
        extra="ignore",
    )

    app_env: str = "development"
    database_url: str = "postgresql+asyncpg://cohortswitch:cohortswitch@localhost:5432/cohortswitch"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "development-secret-change-before-production"
    jwt_algorithm: str = "HS256"
    jwt_access_ttl_seconds: int = 900
    refresh_token_ttl_seconds: int = 2_592_000
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    trusted_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1"])
    cache_ttl_seconds: int = 30
    management_rate_limit: int = 60
    sdk_rate_limit: int = 1000
    max_body_bytes: int = 1_048_576
    log_level: str = "INFO"

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT secret must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def reject_development_secret_in_production(self) -> "Settings":
        if self.app_env == "production" and "development-secret" in self.jwt_secret:
            raise ValueError("A production JWT secret is required")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
