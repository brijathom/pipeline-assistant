import sys

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import import_runs
from app.config import Settings
from app.github import GitHubError, GitHubWorkflowRun
from app.models import PipelineRun


def test_repeated_import_updates_existing_attempts(db_session: Session, github_runs_payload: dict) -> None:
    runs = [GitHubWorkflowRun.model_validate(run) for run in github_runs_payload["workflow_runs"]]
    assert import_runs.upsert_runs(db_session, "example/project", runs) == 2
    db_session.commit()
    original_id = db_session.scalar(select(PipelineRun.id).where(PipelineRun.github_run_id == 3001))

    runs[0] = runs[0].model_copy(update={"status": "completed", "conclusion": "failure"})
    assert import_runs.upsert_runs(db_session, "example/project", runs) == 2
    db_session.commit()

    assert db_session.scalar(select(func.count()).select_from(PipelineRun)) == 2
    saved = db_session.scalars(select(PipelineRun).where(PipelineRun.github_run_id == 3001)).one()
    assert saved.id == original_id
    assert saved.status == "completed"
    assert saved.conclusion == "failure"
    assert saved.started_at == runs[0].run_started_at
    assert saved.created_at == runs[0].created_at
    assert saved.completed_at is None  # updated_at must not become a fabricated finish time.


def test_rerun_is_a_separate_attempt(db_session: Session, github_runs_payload: dict) -> None:
    run = GitHubWorkflowRun.model_validate(github_runs_payload["workflow_runs"][0])
    import_runs.upsert_runs(db_session, "example/project", [run])
    import_runs.upsert_runs(db_session, "example/project", [run.model_copy(update={"run_attempt": 2})])
    attempts = db_session.scalars(select(PipelineRun.run_attempt).order_by(PipelineRun.run_attempt)).all()
    assert attempts == [1, 2]


def test_empty_import_leaves_database_empty(db_session: Session) -> None:
    assert import_runs.upsert_runs(db_session, "example/project", []) == 0
    assert db_session.scalar(select(func.count()).select_from(PipelineRun)) == 0


@pytest.mark.parametrize("problem", ["unconfigured", "github_failure"])
def test_failed_fetch_never_opens_database(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, problem: str,
) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://unused:unused@localhost/unused",
        github_repository="example/project" if problem == "github_failure" else None,
    )
    monkeypatch.setattr(sys, "argv", ["import_runs"])
    monkeypatch.setattr(import_runs, "get_settings", lambda: settings)

    def fail_database():
        pytest.fail("Database must not be opened when configuration or GitHub fetch fails")

    def fail_fetch(repository, limit):
        raise GitHubError("GitHub rate limit reached.")

    monkeypatch.setattr(import_runs, "get_engine", fail_database)
    monkeypatch.setattr(import_runs, "fetch_workflow_runs", fail_fetch)
    assert import_runs.main() == 1
    error = capsys.readouterr().err
    expected = "GITHUB_REPOSITORY" if problem == "unconfigured" else "rate limit"
    assert expected in error


@pytest.mark.parametrize("limit", ["0", "101", "abc"])
def test_cli_rejects_invalid_limit(monkeypatch: pytest.MonkeyPatch, limit: str) -> None:
    monkeypatch.setattr(sys, "argv", ["import_runs", "--limit", limit])
    with pytest.raises(SystemExit) as caught:
        import_runs.main()
    assert caught.value.code == 2
