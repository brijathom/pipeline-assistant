import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import investigation_tasks as tasks
from app.ai_investigation import AIGenerationError
from app.config import Settings, get_settings
from app.database import get_session
from app.investigations import build_report
from app.main import app
from app.models import InvestigationTask, PipelineInvestigation, PipelineInvestigationDraft, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs

TOKEN = "fake-task-reviewer-" + "x" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def source(db_session):
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    job_id = db_session.scalar(select(PipelineJobLog.pipeline_job_id))
    run_id = run.id
    db_session.commit()
    return run_id, job_id


@pytest.fixture
def task_client(runs_client):
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test",
        reviewer_access_token=TOKEN, gemini_api_key="fake-model-key")
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield runs_client
    finally:
        app.dependency_overrides.pop(get_settings, None)


def enqueue(db_session, source, request_id=None):
    task_id = request_id or uuid4()
    tasks.enqueue_task(db_session, task_id, source[0], "gemini-3.5-flash-lite")
    db_session.commit()
    return task_id


async def good_report(bundle, **kwargs):
    return build_report(bundle)


def test_queue_is_idempotent_and_allows_only_one_active_task_per_run(db_session, source):
    task_id = enqueue(db_session, source)
    original = db_session.get(InvestigationTask, task_id)
    created_at, snapshot = original.created_at, original.evidence_snapshot.copy()
    again = tasks.enqueue_task(db_session, task_id, source[0], "gemini-different")
    assert again.id == task_id and again.created_at == created_at and again.evidence_snapshot == snapshot
    assert again.model == "gemini-3.5-flash-lite"
    with pytest.raises(tasks.TaskError, match="already has"):
        tasks.enqueue_task(db_session, uuid4(), source[0], "gemini-3.5-flash-lite")
    with pytest.raises(tasks.TaskError, match="different run"):
        tasks.enqueue_task(db_session, task_id, 999999, "gemini-3.5-flash-lite")


def test_worker_saves_one_unreviewed_draft_and_duplicate_deliveries_do_not_call_model(db_session, source):
    task_id = enqueue(db_session, source)
    bind = db_session.get_bind()
    calls = 0
    async def generate(bundle, **kwargs):
        nonlocal calls
        calls += 1
        assert await tasks.execute_task(task_id, bind, generate=good_report) == "running"
        return build_report(bundle)
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "succeeded"
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "succeeded"
    assert calls == 1
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.started_at and task.completed_at and task.error_code is None
    draft = db_session.get(PipelineInvestigationDraft, task.draft_id)
    assert draft.status == "pending_review" and draft.reviewed_by is None
    assert db_session.get(PipelineInvestigation, source[0]).generation_method == "rules-v1"


@pytest.mark.parametrize("when", ["before", "during"])
def test_changed_evidence_prevents_draft_and_avoids_call_when_already_stale(db_session, source, when):
    task_id = enqueue(db_session, source)
    bind = db_session.get_bind()
    calls = 0
    def withdraw():
        with Session(bind, join_transaction_mode="create_savepoint") as session:
            session.execute(update(PipelineJobLog).where(PipelineJobLog.pipeline_job_id == source[1]).values(is_curated=False))
            session.commit()
    if when == "before": withdraw()
    async def generate(bundle, **kwargs):
        nonlocal calls
        calls += 1
        withdraw()
        return build_report(bundle)
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "failed"
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.error_code == "evidence_changed" and task.draft_id is None
    assert calls == (0 if when == "before" else 1)


@pytest.mark.parametrize("problem", ["provider", "citation", "schema", "timeout"])
def test_failed_generation_is_terminal_safe_and_never_automatically_retried(db_session, source, monkeypatch, problem):
    task_id = enqueue(db_session, source)
    calls = 0
    async def generate(bundle, **kwargs):
        nonlocal calls
        calls += 1
        if problem == "provider": raise AIGenerationError("private-model-output-and-key")
        if problem == "timeout": await asyncio.sleep(1)
        report = build_report(bundle)
        if problem == "citation": report.findings[0].citation.quote = "invented private model output"
        if problem == "schema": report.summary = ""
        return report
    if problem == "timeout": monkeypatch.setattr(tasks, "GENERATION_TIMEOUT_SECONDS", 0.01)
    bind = db_session.get_bind()
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "failed"
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "failed"
    assert calls == 1
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.error_code == "generation_failed" and task.draft_id is None
    assert "private" not in task.error_code
    # Another generation requires a new explicit request ID.
    assert enqueue(db_session, source) != task_id


def test_invalid_snapshot_is_rejected_before_generation(db_session, source):
    task_id = enqueue(db_session, source)
    task = db_session.get(InvestigationTask, task_id)
    task.evidence_snapshot = {**task.evidence_snapshot, "repository": "different/project"}
    db_session.commit()
    async def forbidden(*args, **kwargs): pytest.fail("Invalid snapshot must not call a model")
    assert asyncio.run(tasks.execute_task(task_id, db_session.get_bind(), generate=forbidden)) == "failed"
    db_session.expire_all()
    assert db_session.get(InvestigationTask, task_id).error_code == "invalid_snapshot"


def test_finish_commit_failure_rolls_back_draft_but_never_repeats_external_call(db_session, source, monkeypatch):
    task_id = enqueue(db_session, source)
    commits = 0
    class FailingSession(Session):
        def commit(self):
            nonlocal commits
            commits += 1
            if commits == 2: raise SQLAlchemyError("private database detail")
            super().commit()
    monkeypatch.setattr(tasks, "Session", FailingSession)
    with pytest.raises(SQLAlchemyError):
        asyncio.run(tasks.execute_task(task_id, db_session.get_bind(), generate=good_report))
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.status == "running" and task.draft_id is None
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 0
    async def forbidden(*args, **kwargs): pytest.fail("Ambiguous commit must never repeat a model call")
    assert asyncio.run(tasks.execute_task(task_id, db_session.get_bind(), generate=forbidden)) == "running"


def test_explicit_expiration_fences_a_late_worker_result(db_session, source):
    task_id = enqueue(db_session, source)
    bind = db_session.get_bind()
    async def generate(bundle, **kwargs):
        with Session(bind, join_transaction_mode="create_savepoint") as session:
            with pytest.raises(tasks.TaskError, match="older than"):
                tasks.expire_interrupted_task(session, task_id)
            session.execute(update(InvestigationTask).where(InvestigationTask.id == task_id).values(
                started_at=datetime.now(timezone.utc) - timedelta(seconds=121)))
            tasks.expire_interrupted_task(session, task_id)
            session.commit()
        return build_report(bundle)
    assert asyncio.run(tasks.execute_task(task_id, bind, generate=generate)) == "failed"
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.error_code == "worker_interrupted" and task.draft_id is None
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 0


def test_protected_api_returns_only_task_metadata_and_deduplicates_requests(task_client, db_session, source):
    task_id = uuid4()
    payload = {"request_id": str(task_id), "run_id": source[0]}
    first = task_client.post("/api/reviewer/tasks", headers=AUTH, json=payload)
    second = task_client.post("/api/reviewer/tasks", headers=AUTH, json=payload)
    assert first.status_code == second.status_code == 202 and first.json() == second.json()
    assert first.json()["status"] == "queued" and first.json()["id"] == str(task_id)
    assert first.headers["cache-control"] == "no-store" and "Authorization" in first.headers["vary"]
    assert all(value not in first.text for value in ("evidence_snapshot", "AssertionError", "fake-model-key"))
    assert task_client.get(f"/api/reviewer/tasks/{task_id}", headers=AUTH).json() == first.json()
    assert len(task_client.get("/api/reviewer/tasks?limit=1", headers=AUTH).json()) == 1
    assert task_client.post("/api/reviewer/tasks", headers=AUTH, json={**payload, "request_id": str(uuid4())}).status_code == 409


@pytest.mark.parametrize("method,path", [("GET", "/api/reviewer/tasks"), ("GET", f"/api/reviewer/tasks/{uuid4()}"), ("POST", "/api/reviewer/tasks")])
def test_task_authentication_precedes_database_access(task_client, method, path):
    original = app.dependency_overrides[get_session]
    def forbidden():
        pytest.fail("No database access without reviewer authentication")
        yield
    app.dependency_overrides[get_session] = forbidden
    try:
        assert task_client.request(method, path).status_code == 401
    finally:
        app.dependency_overrides[get_session] = original


def test_task_api_validates_inputs_and_requires_curated_evidence(task_client, db_session, source):
    assert task_client.get(f"/api/reviewer/tasks/{uuid4()}", headers=AUTH).status_code == 404
    assert task_client.get("/api/reviewer/tasks?limit=51", headers=AUTH).status_code == 422
    payload = {"request_id": str(uuid4()), "run_id": source[0], "model": "client-chosen"}
    assert task_client.post("/api/reviewer/tasks", headers=AUTH, json=payload).json() == {"detail": "Invalid reviewer request."}
    db_session.get(PipelineJobLog, source[1]).is_curated = False
    db_session.flush()
    response = task_client.post("/api/reviewer/tasks", headers=AUTH, json={"request_id": str(uuid4()), "run_id": source[0]})
    assert response.status_code == 409
    assert db_session.scalar(select(func.count()).select_from(InvestigationTask)) == 0


def test_unconfigured_ai_does_not_create_a_task(task_client, db_session, source):
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=TOKEN, gemini_api_key=None)
    response = task_client.post("/api/reviewer/tasks", headers=AUTH, json={"request_id": str(uuid4()), "run_id": source[0]})
    assert response.status_code == 503
    assert db_session.scalar(select(func.count()).select_from(InvestigationTask)) == 0


def test_task_worker_through_real_adk_runner_uses_one_fake_model_call(db_session, source):
    pytest.importorskip("google.adk")
    from app.ai_investigation import generate_ai_report
    from app.investigations import EvidenceBundle
    from test_ai_investigation import FakeModel
    task_id = enqueue(db_session, source)
    bundle = EvidenceBundle.model_validate(db_session.get(InvestigationTask, task_id).evidence_snapshot)
    model = FakeModel(text=build_report(bundle).model_dump_json())
    db_session.commit()
    async def sdk_generate(evidence, **kwargs):
        return await generate_ai_report(evidence, model=model)
    assert asyncio.run(tasks.execute_task(task_id, db_session.get_bind(), generate=sdk_generate)) == "succeeded"
    assert model.calls == 1


def test_oversized_utf8_evidence_is_rejected_before_queuing(db_session, source, monkeypatch):
    from app.investigations import collect_evidence
    run = db_session.get(PipelineRun, source[0])
    bundle = collect_evidence(db_session, run)
    bundle.jobs[0].lines[0].text = "雪" * 22000
    monkeypatch.setattr(tasks, "collect_evidence", lambda *args: bundle)
    with pytest.raises(tasks.TaskError, match="64 KiB"):
        tasks.enqueue_task(db_session, uuid4(), source[0], "gemini-3.5-flash-lite")
    assert db_session.scalar(select(func.count()).select_from(InvestigationTask)) == 0
