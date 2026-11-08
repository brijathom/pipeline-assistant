import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.repositories import RepositoryName


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    github_repository: RepositoryName | None = None
    gemini_api_key: SecretStr | None = None
    reviewer_access_token: SecretStr | None = None
    ai_model: str = Field(default="gemini-3.5-flash-lite", pattern=r"^gemini-[a-z0-9.-]{1,80}$")
    pubsub_project_id: str = Field(default="pipeline-assistant-local", pattern=r"^[a-z][a-z0-9-]{4,61}[a-z0-9]$")
    pubsub_topic_id: str = Field(default="investigation-tasks", pattern=r"^[A-Za-z][A-Za-z0-9_-]{2,99}$")
    pubsub_subscription_id: str = Field(default="investigation-worker", pattern=r"^[A-Za-z][A-Za-z0-9_-]{2,99}$")

    @field_validator("reviewer_access_token")
    @classmethod
    def validate_reviewer_access_token(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None or not value.get_secret_value().strip():
            return None
        if re.fullmatch(r"[A-Za-z0-9_-]{43,128}", value.get_secret_value()) is None:
            raise ValueError("Reviewer access requires a URL-safe random token of 43-128 characters.")
        return value

    @field_validator("database_url")
    @classmethod
    def validate_database_url(cls, value: SecretStr) -> SecretStr:
        url = PostgresDsn(value.get_secret_value())
        if url.scheme != "postgresql+psycopg":
            raise ValueError("DATABASE_URL must use postgresql+psycopg")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
