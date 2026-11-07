from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import PipelineRun


def test_empty_metrics_return_zero_counts(runs_client: TestClient) -> None:
    response = runs_client.get("/api/metrics")
    assert response.status_code == 200
    assert response.json() == {
        "repository": None, "total_runs": 0, "successful_runs": 0,
        "failed_runs": 0, "in_progress_runs": 0,
    }


def test_metrics_cover_all_stored_runs_and_filter_repository(
    runs_client: TestClient, db_session: Session,
) -> None:
    cases = [("completed", "success")] * 25 + [
        ("completed", "failure"), ("completed", "timed_out"),
        ("completed", "cancelled"), ("completed", "skipped"), ("completed", "neutral"),
        ("queued", None), ("waiting", None), ("in_progress", None),
        ("completed", None), ("in_progress", "success"),
    ]
    created = datetime(2026, 11, 7, tzinfo=timezone.utc)
    for index, (status, conclusion) in enumerate(cases):
        db_session.add(PipelineRun(
            repository="example/project", github_run_id=4000 + index,
            workflow_name="Build", status=status, conclusion=conclusion, created_at=created,
        ))
    for index in range(5):
        db_session.add(PipelineRun(
            repository="other/project", github_run_id=5000 + index,
            workflow_name="Build", status="completed", conclusion="failure", created_at=created,
        ))
    db_session.flush()

    response = runs_client.get("/api/metrics", params={"repository": " Example/Project "})
    assert response.status_code == 200
    assert response.json() == {
        "repository": "example/project", "total_runs": 35, "successful_runs": 25,
        "failed_runs": 2, "in_progress_runs": 2,
    }
    all_metrics = runs_client.get("/api/metrics")
    assert all_metrics.status_code == 200
    assert all_metrics.json() == {
        "repository": None, "total_runs": 40, "successful_runs": 25,
        "failed_runs": 7, "in_progress_runs": 2,
    }
    # A limited table does not limit the corresponding aggregate.
    rows = runs_client.get("/api/runs", params={"repository": "EXAMPLE/PROJECT", "limit": 3})
    assert rows.status_code == 200
    assert len(rows.json()) == 3
    assert {row["repository"] for row in rows.json()} == {"example/project"}


def test_metrics_count_separate_rerun_attempts(runs_client: TestClient, db_session: Session) -> None:
    for attempt, conclusion in ((1, "failure"), (2, "success")):
        db_session.add(PipelineRun(
            repository="example/project", github_run_id=6000, run_attempt=attempt,
            workflow_name="Build", status="completed", conclusion=conclusion,
            created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
        ))
    db_session.flush()
    assert runs_client.get("/api/metrics").json() == {
        "repository": None, "total_runs": 2, "successful_runs": 1,
        "failed_runs": 1, "in_progress_runs": 0,
    }


def test_unknown_repository_has_empty_metrics_and_runs(runs_client: TestClient) -> None:
    response = runs_client.get("/api/metrics", params={"repository": "unknown/project"})
    assert response.status_code == 200
    assert response.json() == {
        "repository": "unknown/project", "total_runs": 0, "successful_runs": 0,
        "failed_runs": 0, "in_progress_runs": 0,
    }
    assert runs_client.get("/api/runs", params={"repository": "unknown/project"}).json() == []


@pytest.mark.parametrize("endpoint", ["/api/metrics", "/api/runs"])
@pytest.mark.parametrize("repository", ["", "owner/..", "https://github.com/owner/repo", "owner/repo/extra"])
def test_invalid_repository_returns_validation_error(
    runs_client: TestClient, endpoint: str, repository: str,
) -> None:
    assert runs_client.get(endpoint, params={"repository": repository}).status_code == 422
