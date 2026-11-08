from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.models import PipelineJob, PipelineRun
from app.performance import utc_now

NOW = datetime(2026, 11, 8, 12, tzinfo=timezone.utc)


@pytest.fixture
def client(runs_client):
    app.dependency_overrides[utc_now] = lambda: NOW
    try:
        yield runs_client
    finally:
        app.dependency_overrides.pop(utc_now, None)


def add_run(session, created, *, repository="example/project", attempt=1, github_id=100):
    run = PipelineRun(repository=repository, github_run_id=github_id, run_attempt=attempt,
        workflow_name="Build", status="completed", conclusion="success", created_at=created)
    session.add(run)
    session.flush()
    return run


def add_job(session, run, seconds, *, job_id=100, status="completed", conclusion="success", started=None):
    started = started or run.created_at + timedelta(seconds=10)
    job = PipelineJob(pipeline_run_id=run.id, github_job_id=job_id, name="Tests", status=status,
        conclusion=conclusion, started_at=started,
        completed_at=started + timedelta(seconds=seconds) if seconds is not None else None)
    session.add(job)
    session.flush()
    return job


def test_empty_window_has_null_statistics_and_every_utc_day(client):
    response = client.get("/api/metrics/performance", params={"days": 7})
    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 7 and data["repository"] is None
    assert data["start_at"] == "2026-11-02T00:00:00Z" and data["end_at"] == NOW.isoformat().replace("+00:00", "Z")
    assert data["total_jobs"] == data["timed_jobs"] == data["excluded_jobs"] == 0
    assert data["median_job_duration_seconds"] is data["p95_job_duration_seconds"] is None
    assert len(data["daily"]) == 7
    assert data["daily"][0]["date"] == "2026-11-02" and data["daily"][-1]["date"] == "2026-11-08"
    assert all(day["median_job_duration_seconds"] is None for day in data["daily"])


def test_percentiles_use_individual_jobs_not_daily_percentiles_or_pagination(client, db_session):
    run = add_run(db_session, NOW - timedelta(days=1))
    for index in range(25): add_job(db_session, run, 10, job_id=100 + index)
    other = add_run(db_session, NOW - timedelta(days=2), github_id=101)
    add_job(db_session, other, 1000)
    data = client.get("/api/metrics/performance", params={"days": 7}).json()
    assert data["total_jobs"] == data["timed_jobs"] == 26 and data["excluded_jobs"] == 0
    assert data["median_job_duration_seconds"] == data["p95_job_duration_seconds"] == 10
    assert data["daily"][-3]["median_job_duration_seconds"] == 1000
    assert data["daily"][-2]["median_job_duration_seconds"] == 10


def test_interpolated_p95_and_parallel_jobs_are_not_summed(client, db_session):
    run = add_run(db_session, NOW - timedelta(days=1))
    add_job(db_session, run, 60, job_id=1)
    add_job(db_session, run, 600, job_id=2)  # Same start: two parallel jobs, two duration observations.
    data = client.get("/api/metrics/performance").json()
    assert data["median_job_duration_seconds"] == 330
    assert data["p95_job_duration_seconds"] == pytest.approx(573)


def test_invalid_incomplete_skipped_and_future_times_are_excluded_but_real_zero_is_retained(client, db_session):
    run = add_run(db_session, NOW - timedelta(days=1))
    add_job(db_session, run, 0, job_id=1)
    add_job(db_session, run, None, job_id=2)
    missing = add_job(db_session, run, 10, job_id=3)
    missing.started_at = None
    add_job(db_session, run, -1, job_id=4)
    add_job(db_session, run, 10, job_id=5, status="in_progress")
    add_job(db_session, run, 10, job_id=6, started=NOW + timedelta(minutes=1))
    add_job(db_session, run, 10, job_id=7, conclusion="skipped")
    add_job(db_session, run, 10, job_id=8, started=run.created_at - timedelta(seconds=20))
    db_session.flush()
    data = client.get("/api/metrics/performance").json()
    assert data["total_jobs"] == 8 and data["timed_jobs"] == 1 and data["excluded_jobs"] == 7
    assert data["median_job_duration_seconds"] == data["p95_job_duration_seconds"] == 0


def test_window_boundaries_are_creation_cohorts_and_buckets_are_utc(client, db_session):
    # Deliberately change the DB timezone: date grouping must remain UTC, including DST dates.
    db_session.execute(text("SET LOCAL TIME ZONE 'America/Los_Angeles'"))
    start = NOW.replace(hour=0) - timedelta(days=6)
    for index, created in enumerate([start - timedelta(microseconds=1), start, NOW, NOW + timedelta(seconds=1)]):
        run = add_run(db_session, created, github_id=index + 1)
        add_job(db_session, run, 10)
    cross_midnight = datetime(2026, 11, 7, 0, 30, tzinfo=timezone.utc)
    run = add_run(db_session, cross_midnight, github_id=10)
    # Job completion is on another day; cohort stays on the attempt creation day.
    add_job(db_session, run, 10, started=cross_midnight + timedelta(days=1))
    data = client.get("/api/metrics/performance", params={"days": 7}).json()
    assert data["total_jobs"] == data["timed_jobs"] == 2
    assert data["daily"][0]["timed_jobs"] == data["daily"][-2]["timed_jobs"] == 1
    assert data["daily"][-1]["timed_jobs"] == 0


def test_repository_filter_unknown_repo_and_reruns_are_separate_observations(client, db_session):
    for attempt in (1, 2):
        run = add_run(db_session, NOW - timedelta(days=1), attempt=attempt)
        add_job(db_session, run, 60 * attempt)
    other = add_run(db_session, NOW - timedelta(days=1), repository="other/project")
    add_job(db_session, other, 900)
    data = client.get("/api/metrics/performance", params={"repository": " Example/Project "}).json()
    assert data["repository"] == "example/project" and data["timed_jobs"] == 2
    assert data["median_job_duration_seconds"] == 90
    empty = client.get("/api/metrics/performance", params={"repository": "unknown/project"}).json()
    assert empty["total_jobs"] == 0 and empty["median_job_duration_seconds"] is None


@pytest.mark.parametrize("days", [7, 30, 90])
def test_supported_windows_are_bounded(client, days):
    response = client.get("/api/metrics/performance", params={"days": days})
    assert response.status_code == 200 and len(response.json()["daily"]) == days


@pytest.mark.parametrize("params", [{"days": 0}, {"days": 8}, {"days": 365}, {"days": "bad"}, {"repository": "https://invalid.example"}])
def test_invalid_filters_are_rejected(client, params):
    assert client.get("/api/metrics/performance", params=params).status_code == 422


def test_rollup_uses_one_aggregate_query_and_does_not_select_private_data(client, db_session):
    statements = []
    connection = db_session.connection()
    def observe(conn, cursor, statement, parameters, context, executemany): statements.append(statement)
    event.listen(connection, "before_cursor_execute", observe)
    try:
        assert client.get("/api/metrics/performance").status_code == 200
    finally:
        event.remove(connection, "before_cursor_execute", observe)
    queries = [sql for sql in statements if "ROLLUP" in sql.upper()]
    assert len(queries) == 1
    assert all(private not in queries[0] for private in ("pipeline_job_logs", "evidence_snapshot", "report", "pipeline_job_steps"))


def test_database_failure_returns_safe_retryable_error(client, monkeypatch):
    from app import metrics
    def fail(*args): raise SQLAlchemyError("private-password and database query")
    monkeypatch.setattr(metrics, "job_performance", fail)
    response = client.get("/api/metrics/performance")
    assert response.status_code == 503
    assert response.json() == {"detail": "Performance metrics are temporarily unavailable."}
    assert "private-password" not in response.text
