import asyncio
from threading import BoundedSemaphore
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import StreamingResponse

from app import task_events as events
from app.config import Settings, get_settings
from app.main import app
from app.task_queries import list_task_summaries, task_summary_query

TOKEN = "test-task-stream-" + "x" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def client(monkeypatch):
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=TOKEN)
    app.dependency_overrides[events.get_task_snapshot_reader] = lambda: lambda: []
    monkeypatch.setattr(events, "STREAM_SECONDS", 0)
    monkeypatch.setattr(events, "stream_slots", BoundedSemaphore(1))
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_settings, None)
        app.dependency_overrides.pop(events.get_task_snapshot_reader, None)


def test_stream_is_authenticated_private_and_resends_current_snapshot(client):
    for _ in range(2):
        response = client.get("/api/reviewer/tasks/events", headers={**AUTH, "Last-Event-ID": "an-old-snapshot"})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-store"
        assert "Authorization" in response.headers["vary"]
        assert response.headers["x-accel-buffering"] == "no"
        assert "event: tasks\ndata: []\n\n" in response.text
        assert response.text.endswith("event: rotate\ndata: {}\n\n")
    # Two requests can use the one slot in turn: response completion released it.
    assert TOKEN not in response.text


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_unauthorized_stream_never_resolves_snapshot_reader(client, headers):
    def forbidden(): pytest.fail("Unauthenticated stream must not access database or acquire a slot")
    app.dependency_overrides[events.get_task_snapshot_reader] = forbidden
    response = client.get("/api/reviewer/tasks/events?token=not-supported", headers=headers)
    assert response.status_code == 401 and response.headers["cache-control"] == "no-store"
    assert events.stream_slots.acquire(blocking=False)
    events.stream_slots.release()


def test_unconfigured_reviewer_rejects_stream(client):
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None,
        database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=None)
    assert client.get("/api/reviewer/tasks/events", headers=AUTH).status_code == 503


def test_stream_limit_returns_429_before_reading_database(client):
    assert events.stream_slots.acquire(blocking=False)
    read = Mock(side_effect=AssertionError("No database read when capacity is full"))
    app.dependency_overrides[events.get_task_snapshot_reader] = lambda: read
    try:
        response = client.get("/api/reviewer/tasks/events", headers=AUTH)
        assert response.status_code == 429 and response.headers["retry-after"] == "5"
        assert response.headers["cache-control"] == "no-store"
        read.assert_not_called()
    finally:
        events.stream_slots.release()


@pytest.mark.parametrize("failure", [SQLAlchemyError("private connection and password"), ValueError("private snapshot")])
def test_preflight_failure_is_safe_and_releases_slot(client, failure):
    read = Mock(side_effect=failure)
    app.dependency_overrides[events.get_task_snapshot_reader] = lambda: read
    response = client.get("/api/reviewer/tasks/events", headers=AUTH)
    assert response.status_code == 503 and "private" not in response.text
    assert events.stream_slots.acquire(blocking=False)
    events.stream_slots.release()


def test_stream_reads_metadata_only_from_guarded_database(client, db_session):
    from app.investigation_tasks import enqueue_task
    from app.models import PipelineRun
    from app.seed import seed_runs, seed_demo_evidence
    from sqlalchemy import select
    from uuid import uuid4
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    task_id = uuid4()
    enqueue_task(db_session, task_id, run.id, "gemini-3.5-flash-lite")
    db_session.commit()
    app.dependency_overrides[events.get_task_snapshot_reader] = lambda: lambda: list_task_summaries(db_session)
    response = client.get("/api/reviewer/tasks/events", headers=AUTH)
    assert response.status_code == 200 and str(task_id) in response.text and '"status":"queued"' in response.text
    assert all(private not in response.text for private in (TOKEN, "evidence_snapshot", "AssertionError", "report"))
    assert "evidence_snapshot" not in str(task_summary_query())


def test_live_stream_emits_changes_heartbeats_and_stops_on_disconnect(monkeypatch):
    monkeypatch.setattr(events, "POLL_SECONDS", 0)
    monkeypatch.setattr(events, "HEARTBEAT_SECONDS", 0)
    frames = iter(["changed snapshot", "changed snapshot"])
    monkeypatch.setattr(events, "snapshot_frame", lambda _: next(frames))
    class Request:
        disconnected = False
        async def is_disconnected(self): return self.disconnected
    async def exercise():
        request = Request()
        stream = events.task_events(request, lambda: [], "initial snapshot")
        assert await anext(stream) == "initial snapshot"
        assert await anext(stream) == "changed snapshot"
        assert await anext(stream) == ": keep-alive\n\n"
        request.disconnected = True
        with pytest.raises(StopAsyncIteration): await anext(stream)
    asyncio.run(exercise())


def test_midstream_failure_sends_safe_error_then_closes(monkeypatch):
    monkeypatch.setattr(events, "POLL_SECONDS", 0)
    class Request:
        async def is_disconnected(self): return False
    async def exercise():
        stream = events.task_events(Request(), Mock(side_effect=SQLAlchemyError("private SQL")), "initial")
        assert await anext(stream) == "initial"
        assert await anext(stream) == "event: stream-error\ndata: {}\n\n"
        with pytest.raises(StopAsyncIteration): await anext(stream)
    asyncio.run(exercise())


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), RuntimeError("send failed")])
def test_response_releases_slot_even_if_cancelled_before_body_iteration(monkeypatch, failure):
    slots = BoundedSemaphore(1)
    slots.acquire()
    monkeypatch.setattr(events, "stream_slots", slots)
    async def fail(*args): raise failure
    monkeypatch.setattr(StreamingResponse, "__call__", fail)
    async def exercise():
        response = events.TaskEventResponse(iter([]))
        with pytest.raises(type(failure)): await response({}, None, None)
    asyncio.run(exercise())
    assert slots.acquire(blocking=False)
    slots.release()


def test_snapshot_has_a_stable_version_and_rejects_oversize(monkeypatch):
    assert events.snapshot_frame([]) == events.snapshot_frame([])
    with pytest.raises(ValueError): events.snapshot_frame([{}] * 21)
    monkeypatch.setattr(events, "MAX_FRAME_BYTES", 1)
    with pytest.raises(ValueError): events.snapshot_frame([])


def test_real_http_stream_flushes_snapshot_and_releases_capacity_on_disconnect(client, monkeypatch):
    import socket
    import threading
    import time
    import httpx
    import uvicorn
    monkeypatch.setattr(events, "STREAM_SECONDS", 30)
    monkeypatch.setattr(events, "POLL_SECONDS", 0.01)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert server.started
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=3, trust_env=False) as http:
            with http.stream("GET", "/api/reviewer/tasks/events", headers=AUTH) as response:
                assert response.status_code == 200
                lines = response.iter_lines()
                assert next(lines).startswith("id: ")
                assert next(lines) == "event: tasks"
                assert next(lines) == "data: []"
                assert next(lines) == ""
                assert not events.stream_slots.acquire(blocking=False)
            # Closing before the 30-second lifetime must release the sole slot.
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                if events.stream_slots.acquire(blocking=False):
                    events.stream_slots.release()
                    break
                time.sleep(0.01)
            else:
                pytest.fail("Disconnected HTTP stream leaked its capacity slot")
            assert http.get("/health").status_code == 200
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
        assert not thread.is_alive()
