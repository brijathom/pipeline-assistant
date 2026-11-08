import pytest
from pydantic import ValidationError

from app.config import Settings


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_database_url_hides_password() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:private-password@localhost/test",
    )
    assert "private-password" not in repr(settings)


def test_optional_ai_key_is_hidden_and_model_is_validated() -> None:
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", gemini_api_key="fake-private-key")
    assert "fake-private-key" not in repr(settings)
    assert settings.ai_model == "gemini-3.8-flash"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", ai_model="https://untrusted.invalid")


def test_database_url_rejects_other_drivers() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql://user:password@localhost/test")


def test_repository_name_is_normalized() -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/test",
        github_repository=" Example/Project ",
    )
    assert settings.github_repository == "example/project"


@pytest.mark.parametrize("repository", [
    "https://github.com/owner/repo", "owner/repo/extra", "owner/..", "owner/.",
    "owner/repo?query=1", "owner%2frepo", "", "owner/repo#fragment",
])
def test_repository_must_be_a_name_not_a_url(repository: str) -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            database_url="postgresql+psycopg://user:password@localhost/test",
            github_repository=repository,
        )
