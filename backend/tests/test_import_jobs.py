import sys
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import import_jobs
from app.github import GitHubError, GitHubJob
from app.models import PipelineJob, PipelineJobStep, PipelineRun


def add_run(session: Session, attempt: int = 2, repository: str = "example/project") -> PipelineRun:
    run = PipelineRun(
        repository=repository, github_run_id=3001, run_attempt=attempt,
        workflow_name="Build", status="in_progress",
        created_at=datetime(2026, 11, 7, 12, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def parse_jobs(payload: dict) -> list[GitHubJob]:
    return [GitHubJob.model_validate(job) for job in payload["jobs"]]


@pytest.fixture
def command_sessions(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> list[Session]:
    sessions = []
    monkeypatch.setattr(import_jobs, "get_engine", lambda: db_session.get_bind())

    def session_factory(bind):
        # Each command session has its own savepoint inside the fixture's rollback boundary.
        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        sessions.append(session)
        return session

    monkeypatch.setattr(import_jobs, "Session", session_factory)
    return sessions


def test_repeated_job_imports_update_without_duplicates(db_session: Session, github_jobs_payload: dict) -> None:
    run = add_run(db_session)
    assert import_jobs.upsert_jobs(db_session, run.id, parse_jobs(github_jobs_payload)) == (2, 2)
    db_session.commit()
    original_job = db_session.scalars(select(PipelineJob).where(PipelineJob.github_job_id == 7001)).one()
    original_step = db_session.scalars(select(PipelineJobStep).where(PipelineJobStep.step_number == 7)).one()
    job_id, step_id, ingested_at = original_job.id, original_step.id, original_job.ingested_at

    updated = deepcopy(github_jobs_payload)
    updated["jobs"][0]["name"] = "Build (linux, updated)"
    updated["jobs"][0]["conclusion"] = "success"
    updated["jobs"][0]["steps"][1]["name"] = "Run updated tests"
    updated["jobs"][0]["steps"][1]["conclusion"] = "success"
    updated["jobs"][1]["steps"] = [deepcopy(updated["jobs"][0]["steps"][0])]
    assert import_jobs.upsert_jobs(db_session, run.id, parse_jobs(updated)) == (2, 3)
    db_session.commit()
    db_session.expire_all()
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 2
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 3
    saved_job = db_session.get(PipelineJob, job_id)
    saved_step = db_session.get(PipelineJobStep, step_id)
    assert saved_job.name == "Build (linux, updated)"
    assert saved_job.conclusion == "success"
    assert saved_job.ingested_at == ingested_at
    assert saved_job.completed_at == parse_jobs(updated)[0].completed_at
    assert saved_step.name == "Run updated tests"
    assert saved_step.conclusion == "success"
    assert saved_step.completed_at == parse_jobs(updated)[0].steps[1].completed_at
    # Job timing does not invent a workflow completion timestamp.
    assert db_session.get(PipelineRun, run.id).completed_at is None


def test_different_attempts_keep_separate_jobs_and_steps(db_session: Session, github_jobs_payload: dict) -> None:
    first_run = add_run(db_session, attempt=1)
    second_run = add_run(db_session, attempt=2)
    first_jobs = parse_jobs(github_jobs_payload)
    for job in first_jobs:
        job.run_attempt = 1
    import_jobs.upsert_jobs(db_session, first_run.id, first_jobs)
    import_jobs.upsert_jobs(db_session, second_run.id, parse_jobs(github_jobs_payload))
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 4
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 4
    assert set(db_session.scalars(select(PipelineJob.pipeline_run_id))) == {first_run.id, second_run.id}


def test_empty_or_smaller_snapshots_preserve_observed_evidence(db_session: Session, github_jobs_payload: dict) -> None:
    run = add_run(db_session)
    jobs = parse_jobs(github_jobs_payload)
    import_jobs.upsert_jobs(db_session, run.id, jobs)
    jobs[0].steps = jobs[0].steps[:1]
    assert import_jobs.upsert_jobs(db_session, run.id, jobs[:1]) == (1, 1)
    assert import_jobs.upsert_jobs(db_session, run.id, []) == (0, 0)
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 2
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 2


def test_command_fetches_outside_transactions_then_commits(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    db_session: Session, command_sessions: list[Session], github_jobs_payload: dict,
) -> None:
    run = add_run(db_session)
    monkeypatch.setattr(sys, "argv", ["import_jobs", "--run-id", str(run.id)])

    def fetch(repository, run_id, run_attempt):
        assert (repository, run_id, run_attempt) == ("example/project", 3001, 2)
        assert len(command_sessions) == 1
        assert not command_sessions[0].in_transaction()
        return parse_jobs(github_jobs_payload)

    monkeypatch.setattr(import_jobs, "fetch_workflow_jobs", fetch)
    assert import_jobs.main() == 0
    assert len(command_sessions) == 2
    assert all(not session.in_transaction() for session in command_sessions)
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 2
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 2
    assert "2 jobs and 2 steps" in capsys.readouterr().out


@pytest.mark.parametrize("problem", ["missing", "demo"])
def test_unavailable_or_fictional_runs_never_contact_github(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    db_session: Session, command_sessions: list[Session], problem: str,
) -> None:
    run_id = add_run(db_session, repository="demo/project").id if problem == "demo" else 9_223_372_036_854_775_807
    monkeypatch.setattr(sys, "argv", ["import_jobs", "--run-id", str(run_id)])

    def fail_fetch(*args):
        pytest.fail("Missing or fictional runs must not contact GitHub")

    monkeypatch.setattr(import_jobs, "fetch_workflow_jobs", fail_fetch)
    assert import_jobs.main() == 1
    expected = "Fictional demo" if problem == "demo" else "Stored run not found"
    assert expected in capsys.readouterr().err
    assert len(command_sessions) == 1


def test_failed_fetch_keeps_existing_evidence_and_never_opens_write_session(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    db_session: Session, command_sessions: list[Session], github_jobs_payload: dict,
) -> None:
    run = add_run(db_session)
    import_jobs.upsert_jobs(db_session, run.id, parse_jobs(github_jobs_payload))
    monkeypatch.setattr(sys, "argv", ["import_jobs", "--run-id", str(run.id)])

    def fail_fetch(*args):
        assert not command_sessions[0].in_transaction()
        raise GitHubError("GitHub returned an incomplete job page. No jobs were saved.")

    monkeypatch.setattr(import_jobs, "fetch_workflow_jobs", fail_fetch)
    assert import_jobs.main() == 1
    assert len(command_sessions) == 1
    assert "incomplete" in capsys.readouterr().err
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 2
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 2


@pytest.mark.parametrize("change", ["deleted", "changed_attempt"])
def test_run_identity_is_rechecked_after_network_access(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    db_session: Session, command_sessions: list[Session], github_jobs_payload: dict, change: str,
) -> None:
    run = add_run(db_session)
    run_id = run.id
    monkeypatch.setattr(sys, "argv", ["import_jobs", "--run-id", str(run_id)])

    def fetch(*args):
        if change == "deleted":
            db_session.execute(delete(PipelineRun).where(PipelineRun.id == run_id))
        else:
            run.run_attempt = 3
            db_session.flush()
        return parse_jobs(github_jobs_payload)

    monkeypatch.setattr(import_jobs, "fetch_workflow_jobs", fetch)
    assert import_jobs.main() == 1
    assert "changed during the fetch" in capsys.readouterr().err
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 0


def test_database_failure_rolls_back_all_evidence_and_hides_internal_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    db_session: Session, command_sessions: list[Session], github_jobs_payload: dict,
) -> None:
    run = add_run(db_session)
    monkeypatch.setattr(sys, "argv", ["import_jobs", "--run-id", str(run.id)])
    monkeypatch.setattr(import_jobs, "fetch_workflow_jobs", lambda *args: parse_jobs(github_jobs_payload))
    save = import_jobs.upsert_jobs

    def fail_after_write(session, run_id, jobs):
        save(session, run_id, jobs)
        raise SQLAlchemyError("internal-database-password")

    monkeypatch.setattr(import_jobs, "upsert_jobs", fail_after_write)
    assert import_jobs.main() == 1
    error = capsys.readouterr().err
    assert "Could not import jobs" in error
    assert "internal-database-password" not in error
    assert db_session.scalar(select(func.count()).select_from(PipelineJob)) == 0
    assert db_session.scalar(select(func.count()).select_from(PipelineJobStep)) == 0


@pytest.mark.parametrize("value", [None, "0", "-1", "abc", "9223372036854775808"])
def test_invalid_command_arguments_never_open_database(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    monkeypatch.setattr(sys, "argv", ["import_jobs"] + ([] if value is None else ["--run-id", value]))

    def fail_database():
        pytest.fail("Invalid arguments must not open the database")

    monkeypatch.setattr(import_jobs, "get_engine", fail_database)
    with pytest.raises(SystemExit) as error:
        import_jobs.main()
    assert error.value.code == 2
