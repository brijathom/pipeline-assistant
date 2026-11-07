import re
from functools import lru_cache
from pathlib import Path

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[1] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    database_url: SecretStr
    github_repository: str | None = None

    @field_validator("github_repository")
    @classmethod
    def validate_github_repository(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,38}/[a-z0-9_.-]{1,100}", value):
            raise ValueError("GITHUB_REPOSITORY must be a GitHub owner/repository name")
        if value.split("/")[1] in {".", ".."}:
            raise ValueError("GITHUB_REPOSITORY must contain a repository name")
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
