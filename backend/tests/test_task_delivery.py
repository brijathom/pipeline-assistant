import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import task_delivery as delivery
from app.investigation_tasks import enqueue_task
from app.investigations import build_report
from app.models import InvestigationTask, PipelineInvestigationDraft, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


@pytest.fixture
def task_id(db_session):
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    task_id = uuid4()
    enqueue_task(db_session, task_id, run.id, "gemini-3.5-flash-lite")
    db_session.commit()
    return task_id


def test_dispatch_publishes_only_uuid_after_committed_attempt_and_once_confirmed(db_session, task_id):
    bind = db_session.get_bind()
    sent = []
    def publish(data):
        with Session(bind, join_transaction_mode="create_savepoint") as session:
            task = session.get(InvestigationTask, task_id)
            assert task.dispatch_attempts == 1 and task.dispatched_at is None
            assert task.dispatch_after > datetime.now(timezone.utc)
        sent.append(data)
        assert delivery.dispatch_one(bind, publish) is None  # Lease prevents concurrent selection.
    assert delivery.dispatch_one(bind, publish) == (task_id, True)
    assert delivery.dispatch_one(bind, publish) is None
    assert sent == [str(task_id).encode("ascii")]
    db_session.expire_all()
    assert db_session.get(InvestigationTask, task_id).dispatched_at is not None


def test_ambiguous_publish_retries_are_delayed_and_bounded(db_session, task_id):
    bind = db_session.get_bind()
    def fail(data):
        raise RuntimeError("private infrastructure error")
    for attempt in range(1, 6):
        assert delivery.dispatch_one(bind, fail) == (task_id, False)
        db_session.expire_all()
        task = db_session.get(InvestigationTask, task_id)
        assert task.dispatch_attempts == attempt and task.dispatched_at is None
        assert delivery.dispatch_one(bind, fail) is None
        db_session.execute(update(InvestigationTask).where(InvestigationTask.id == task_id).values(
            dispatch_after=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db_session.commit()
    assert delivery.dispatch_one(bind, fail) is None
    assert db_session.get(InvestigationTask, task_id).status == "queued"


def test_lost_publish_confirmation_can_redeliver_without_second_model_call(db_session, task_id):
    bind = db_session.get_bind()
    messages = []
    # A broker accepted publication, but the caller lost the response.
    def uncertain(data):
        messages.append(data)
        raise TimeoutError()
    assert delivery.dispatch_one(bind, uncertain) == (task_id, False)
    db_session.execute(update(InvestigationTask).where(InvestigationTask.id == task_id).values(
        dispatch_after=datetime.now(timezone.utc) - timedelta(seconds=1)))
    db_session.commit()
    assert delivery.dispatch_one(bind, messages.append) == (task_id, True)
    calls = 0
    async def generate(bundle, **kwargs):
        nonlocal calls
        calls += 1
        return build_report(bundle)
    for data in messages:
        assert asyncio.run(delivery.handle_message(data, bind, generate=generate)) == "succeeded"
    assert calls == 1
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert db_session.get(PipelineInvestigationDraft, task.draft_id).status == "pending_review"


def test_failed_dispatch_confirmation_commit_keeps_original_attempt_for_recovery(db_session, task_id, monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    original_commit = Session.commit
    commits = 0
    def commit(session):
        nonlocal commits
        commits += 1
        if commits == 2:
            raise SQLAlchemyError("private database failure")
        return original_commit(session)
    monkeypatch.setattr(Session, "commit", commit)
    messages = []
    with pytest.raises(SQLAlchemyError): delivery.dispatch_one(db_session.get_bind(), messages.append)
    db_session.expire_all()
    task = db_session.get(InvestigationTask, task_id)
    assert task.dispatch_attempts == 1 and task.dispatched_at is None
    assert messages == [str(task_id).encode()]


@pytest.mark.parametrize("data", [b"", b"not-a-task", b"x" * 65536, b"\xff" * 36, b"0" * 36, str(uuid4()).upper().encode()])
def test_malformed_messages_never_touch_database(data):
    assert asyncio.run(delivery.handle_message(data, None)) == "discarded"


def test_unknown_task_is_discarded_without_model_call(db_session):
    async def forbidden(*args, **kwargs):
        pytest.fail("Unknown task must not call provider")
    assert asyncio.run(delivery.handle_message(str(uuid4()).encode(), db_session.get_bind(), generate=forbidden)) == "missing"


def test_nonqueued_tasks_are_not_dispatched(db_session, task_id):
    from app.investigation_tasks import execute_task
    async def generate(bundle, **kwargs):
        return build_report(bundle)
    asyncio.run(execute_task(task_id, db_session.get_bind(), generate=generate))
    assert delivery.dispatch_one(db_session.get_bind(), lambda _: pytest.fail("Terminal task dispatched")) is None


def test_exhausted_dispatch_is_visible_in_protected_task_status(db_session, task_id, runs_client):
    from app.config import Settings, get_settings
    from app.main import app
    token = "test-dispatch-" + "x" * 43
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=token)
    try:
        db_session.execute(update(InvestigationTask).where(InvestigationTask.id == task_id).values(dispatch_attempts=5))
        db_session.commit()
        response = runs_client.get(f"/api/reviewer/tasks/{task_id}", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200 and response.json()["dispatch_exhausted"] is True
        assert response.json()["status"] == "queued"
        assert "evidence_snapshot" not in response.json()
    finally:
        app.dependency_overrides.pop(get_settings, None)
