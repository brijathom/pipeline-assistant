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


def test_database_url_rejects_other_drivers() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, database_url="postgresql://user:password@localhost/test")
