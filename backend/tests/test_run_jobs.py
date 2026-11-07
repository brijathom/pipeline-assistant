from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.models import PipelineJob, PipelineJobStep, PipelineRun
from app.queries import list_run_jobs


def add_run(session: Session, attempt: int) -> PipelineRun:
    run = PipelineRun(
        repository="example/project", github_run_id=3001, run_attempt=attempt,
        workflow_name="Build", status="completed", conclusion="failure",
        created_at=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def test_jobs_response_orders_evidence_and_isolates_attempts(runs_client: TestClient, db_session: Session) -> None:
    first_run = add_run(db_session, 1)
    other_run = add_run(db_session, 2)
    jobs = []
    for run, github_id in ((first_run, 7002), (first_run, 7001), (other_run, 7003)):
        job = PipelineJob(
            pipeline_run_id=run.id, github_job_id=github_id, name=f"Build {github_id}",
            status="completed", conclusion="failure",
            started_at=datetime(2026, 11, 7, 12, 0, 10, tzinfo=timezone.utc),
            completed_at=datetime(2026, 11, 7, 12, 3, tzinfo=timezone.utc),
        )
        db_session.add(job)
        db_session.flush()
        jobs.append(job)
    for job in jobs:
        for number in (7, 1):
            db_session.add(PipelineJobStep(
                pipeline_job_id=job.id, step_number=number, name=f"Step {number}",
                status="completed", conclusion="failure" if number == 7 else "success",
                started_at=job.started_at, completed_at=job.completed_at,
            ))
    db_session.flush()

    response = runs_client.get(f"/api/runs/{first_run.id}/jobs")
    assert response.status_code == 200
    payload = response.json()
    assert [job["github_job_id"] for job in payload] == [7001, 7002]
    for job in payload:
        assert set(job) == {
            "id", "github_job_id", "name", "status", "conclusion", "started_at", "completed_at", "steps",
        }
        assert job["status"] == "completed"
        assert job["conclusion"] == "failure"
        assert job["started_at"] == "2026-11-07T12:00:10Z"
        assert job["completed_at"] == "2026-11-07T12:03:00Z"
        assert [step["step_number"] for step in job["steps"]] == [1, 7]
        assert [step["conclusion"] for step in job["steps"]] == ["success", "failure"]
        assert job["steps"][0]["started_at"] == "2026-11-07T12:00:10Z"
        assert job["steps"][1]["completed_at"] == "2026-11-07T12:03:00Z"
        assert set(job["steps"][0]) == {
            "id", "step_number", "name", "status", "conclusion", "started_at", "completed_at",
        }
    other = runs_client.get(f"/api/runs/{other_run.id}/jobs")
    assert other.status_code == 200
    assert [job["github_job_id"] for job in other.json()] == [7003]


def test_valid_run_without_stored_jobs_is_empty(runs_client: TestClient, db_session: Session) -> None:
    run = add_run(db_session, 1)
    response = runs_client.get(f"/api/runs/{run.id}/jobs")
    assert response.status_code == 200
    assert response.json() == []


def test_job_without_steps_preserves_unknown_timestamps(runs_client: TestClient, db_session: Session) -> None:
    run = add_run(db_session, 1)
    job = PipelineJob(pipeline_run_id=run.id, github_job_id=7001, name="Waiting", status="queued")
    db_session.add(job)
    db_session.flush()
    response = runs_client.get(f"/api/runs/{run.id}/jobs")
    assert response.status_code == 200
    assert response.json() == [{
        "id": job.id, "github_job_id": 7001, "name": "Waiting", "status": "queued",
        "conclusion": None, "started_at": None, "completed_at": None, "steps": [],
    }]


def test_unknown_parent_returns_not_found(runs_client: TestClient) -> None:
    response = runs_client.get("/api/runs/9223372036854775807/jobs")
    assert response.status_code == 404
    assert response.json() == {"detail": "Pipeline run not found"}


@pytest.mark.parametrize("run_id", ["0", "-1", "abc", "9223372036854775808"])
def test_invalid_parent_id_is_rejected(runs_client: TestClient, run_id: str) -> None:
    assert runs_client.get(f"/api/runs/{run_id}/jobs").status_code == 422


def test_evidence_query_count_is_constant_as_jobs_grow(db_session: Session) -> None:
    run = add_run(db_session, 1)
    counts = []
    connection = db_session.connection()

    def capture(conn, cursor, statement, parameters, context, executemany):
        counts.append(statement)

    previous_total = 0
    for total in (1, 5):
        for index in range(previous_total, total):
            job = PipelineJob(
                pipeline_run_id=run.id, github_job_id=7000 + index, name=f"Build {index}", status="queued",
            )
            db_session.add(job)
            db_session.flush()
            db_session.add(PipelineJobStep(
                pipeline_job_id=job.id, step_number=1, name="Test", status="queued",
            ))
        db_session.flush()
        counts.clear()
        event.listen(connection, "before_cursor_execute", capture)
        try:
            payload = list_run_jobs(db_session, run.id)
            assert len(payload) == total
            assert all(len(job.steps) == 1 for job in payload)
            assert len(counts) == 2
        finally:
            event.remove(connection, "before_cursor_execute", capture)
        previous_total = total
