from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import PipelineRun
from app.seed import seed_runs


def test_empty_database_returns_empty_list(runs_client: TestClient) -> None:
    response = runs_client.get("/api/runs")
    assert response.status_code == 200
    assert response.json() == []


def test_runs_are_ordered_and_limited(runs_client: TestClient, db_session: Session) -> None:
    created = datetime(2026, 11, 7, tzinfo=timezone.utc)
    runs = []
    # The last inserted row is older, so sorting by id alone cannot pass.
    for index, days in enumerate((2, 0, 2, 1)):
        run = PipelineRun(
            repository="demo/ordering-test",
            github_run_id=2000 + index,
            workflow_name="Demo workflow",
            status="queued",
            created_at=created + timedelta(days=days),
        )
        db_session.add(run)
        db_session.flush()
        runs.append(run)

    response = runs_client.get("/api/runs?limit=3")
    assert response.status_code == 200
    assert [run["id"] for run in response.json()] == [runs[2].id, runs[0].id, runs[3].id]


def test_run_response_fields(runs_client: TestClient, db_session: Session) -> None:
    seed_runs(db_session)
    response = runs_client.get("/api/runs")
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 3
    assert rows[0] == {
        "id": rows[0]["id"],
        "repository": "demo/pipeline-assistant",
        "github_run_id": 1003,
        "run_attempt": 1,
        "workflow_name": "Demo: Build and Test",
        "status": "in_progress",
        "conclusion": None,
        "created_at": "2026-11-07T12:00:00Z",
        "started_at": "2026-11-07T12:00:10Z",
        "completed_at": None,
    }
    assert isinstance(rows[0]["id"], int)
    assert rows[2]["conclusion"] == "success"
    assert rows[2]["completed_at"] == "2026-11-07T10:03:00Z"


@pytest.mark.parametrize("limit", ["0", "101", "-1", "abc"])
def test_invalid_limit_returns_validation_error(runs_client: TestClient, limit: str) -> None:
    assert runs_client.get(f"/api/runs?limit={limit}").status_code == 422
