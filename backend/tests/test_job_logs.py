from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.log_evidence import save_log_excerpt
from app.log_processing import process_log
from app.models import PipelineJob, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


def make_job(session: Session, attempt: int = 1) -> PipelineJob:
    run = PipelineRun(
        repository="example/project", github_run_id=1234, run_attempt=attempt,
        workflow_name="Build", status="completed", conclusion="failure",
        created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    job = PipelineJob(pipeline_run_id=run.id, github_job_id=5678, name="Tests", status="completed")
    session.add(job)
    session.flush()
    return job


def test_curated_api_preserves_numbered_redacted_evidence(runs_client: TestClient, db_session: Session) -> None:
    job = make_job(db_session)
    excerpt = process_log(b"setup\nTOKEN=fake-secret\n\nFAILED test_health\n", start_line=2)
    save_log_excerpt(db_session, job.id, excerpt, source_kind="demo", is_curated=True)
    response = runs_client.get(f"/api/runs/{job.pipeline_run_id}/jobs/{job.id}/log")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["lines"] == [
        {"source_line": 2, "text": "TOKEN=[REDACTED]"},
        {"source_line": 3, "text": ""}, {"source_line": 4, "text": "FAILED test_health"},
    ]
    assert payload["total_source_lines"] == 4
    assert payload["truncated"] and payload["redactions_applied"]
    assert payload["source_kind"] == "demo"
    assert set(payload) == {"lines", "total_source_lines", "truncated", "redactions_applied", "source_kind", "updated_at"}
    record = db_session.get(PipelineJobLog, job.id)
    assert "fake-secret" not in record.text
    assert record.updated_at.tzinfo is not None


@pytest.mark.parametrize("stored", [False, True])
def test_missing_and_uncurated_logs_are_indistinguishable_to_public_api(
    runs_client: TestClient, db_session: Session, stored: bool,
) -> None:
    job = make_job(db_session)
    if stored:
        save_log_excerpt(db_session, job.id, process_log(b"unreviewed-custom-sensitive-value"))
    response = runs_client.get(f"/api/runs/{job.pipeline_run_id}/jobs/{job.id}/log")
    assert response.status_code == 200
    assert response.json() is None
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("raw, expected", [(b"", []), (b"\n", [{"source_line": 1, "text": ""}])])
def test_empty_log_is_distinct_from_absent_log(
    runs_client: TestClient, db_session: Session, raw: bytes, expected: list,
) -> None:
    job = make_job(db_session)
    save_log_excerpt(db_session, job.id, process_log(raw), source_kind="demo", is_curated=True)
    payload = runs_client.get(f"/api/runs/{job.pipeline_run_id}/jobs/{job.id}/log").json()
    assert payload["lines"] == expected
    assert not payload["truncated"]


def test_replacement_revokes_curation_and_preserves_one_record(runs_client: TestClient, db_session: Session) -> None:
    job = make_job(db_session)
    save_log_excerpt(db_session, job.id, process_log(b"old curated content"), is_curated=True)
    save_log_excerpt(db_session, job.id, process_log(b"TOKEN=new-fake-secret\nnew failure"))
    assert db_session.scalar(select(func.count()).select_from(PipelineJobLog)) == 1
    db_session.expire_all()
    record = db_session.get(PipelineJobLog, job.id)
    assert record.text == "TOKEN=[REDACTED]\nnew failure"
    assert not record.is_curated
    assert record.source_kind == "github"
    assert runs_client.get(f"/api/runs/{job.pipeline_run_id}/jobs/{job.id}/log").json() is None


def test_log_lookup_cannot_cross_attempt_or_job_boundaries(runs_client: TestClient, db_session: Session) -> None:
    first, second = make_job(db_session, 1), make_job(db_session, 2)
    save_log_excerpt(db_session, first.id, process_log(b"first attempt"), is_curated=True)
    for run_id, job_id in ((second.pipeline_run_id, first.id), (first.pipeline_run_id, second.id), (2**63 - 1, first.id), (first.pipeline_run_id, 2**63 - 1)):
        assert runs_client.get(f"/api/runs/{run_id}/jobs/{job_id}/log").status_code == 404


@pytest.mark.parametrize("field", ["run", "job"])
@pytest.mark.parametrize("value", ["0", "-1", "abc", "9223372036854775808"])
def test_invalid_route_ids_are_rejected(runs_client: TestClient, field: str, value: str) -> None:
    path = f"/api/runs/{value}/jobs/1/log" if field == "run" else f"/api/runs/1/jobs/{value}/log"
    assert runs_client.get(path).status_code == 422


@pytest.mark.parametrize("parent", ["run", "job"])
def test_deleting_parent_cascades_only_its_excerpt(db_session: Session, parent: str) -> None:
    first, second = make_job(db_session, 1), make_job(db_session, 2)
    for job in (first, second):
        save_log_excerpt(db_session, job.id, process_log(b"processed evidence"))
    if parent == "run":
        db_session.execute(delete(PipelineRun).where(PipelineRun.id == first.pipeline_run_id))
    else:
        db_session.execute(delete(PipelineJob).where(PipelineJob.id == first.id))
    assert list(db_session.scalars(select(PipelineJobLog.pipeline_job_id))) == [second.id]


@pytest.mark.parametrize("changes, constraint", [
    ({"text": "x" * 16385}, "ck_pipeline_job_logs_bytes"),
    ({"line_count": 101, "total_source_lines": 101}, "ck_pipeline_job_logs_lines"),
    ({"start_line": 0}, "ck_pipeline_job_logs_source_range"),
    ({"total_source_lines": 0}, "ck_pipeline_job_logs_source_range"),
    ({"source_kind": "unknown"}, "ck_pipeline_job_logs_source_kind"),
    ({"pipeline_job_id": 2**63 - 1}, "fk_pipeline_job_logs_job"),
])
def test_database_enforces_log_boundaries(db_session: Session, changes: dict, constraint: str) -> None:
    job = make_job(db_session)
    row = dict(pipeline_job_id=job.id, text="safe", start_line=1, line_count=1,
               total_source_lines=1, truncated=False, redactions_applied=False, source_kind="github")
    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            db_session.add(PipelineJobLog(**{**row, **changes}))
            db_session.flush()
    assert error.value.orig.diag.constraint_name == constraint


def test_demo_evidence_is_idempotent_and_contains_only_processed_fixture_content(db_session: Session) -> None:
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    original = db_session.scalar(select(PipelineJobLog))
    original_job_id = original.pipeline_job_id
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    assert db_session.scalar(select(func.count()).select_from(PipelineJobLog)) == 1
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 1
    assert original.pipeline_job_id == original_job_id
    assert original.source_kind == "demo" and original.is_curated
    assert "demo-only-placeholder" not in original.text
    assert "[REDACTED]" in original.text and "expected HTTP 200" in original.text
