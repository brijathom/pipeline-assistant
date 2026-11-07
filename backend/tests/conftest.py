import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, delete
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import get_session
from app.main import app
from app.models import PipelineRun


@pytest.fixture
def github_runs_payload() -> dict:
    fixture = Path(__file__).parent / "fixtures" / "github_runs.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def test_engine() -> Iterator[Engine]:
    database_url = os.environ.get("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("Set TEST_DATABASE_URL to run PostgreSQL integration tests")
    url = make_url(database_url)
    if (
        url.drivername != "postgresql+psycopg"
        or url.host not in {"localhost", "127.0.0.1"}
        or url.port != 5433
        or url.database != "pipeline_assistant_test"
        or url.username != "pipeline_test"
    ):
        pytest.fail("Tests require the dedicated local pipeline_assistant_test database on port 5433")
    engine = create_engine(url, connect_args={"connect_timeout": 5}, hide_parameters=True)
    try:
        config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
        with engine.begin() as connection:
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Iterator[Session]:
    with test_engine.connect() as connection:
        transaction = connection.begin()
        with Session(bind=connection, join_transaction_mode="create_savepoint") as session:
            # Only the guarded test database is touched; cleanup is rolled back.
            session.execute(delete(PipelineRun))
            yield session
        transaction.rollback()


@pytest.fixture
def runs_client(db_session: Session) -> Iterator[TestClient]:
    def override_session():
        yield db_session

    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)
