import sys
import subprocess

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import import_logs
from app.github import GitHubError
from app.log_evidence import save_log_excerpt
from app.log_processing import process_log
from app.models import PipelineJobLog, PipelineRun
from test_job_logs import make_job


@pytest.fixture
def command_sessions(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> list[Session]:
    sessions = []
    monkeypatch.setattr(import_logs, "get_engine", lambda: db_session.get_bind())

    def session_factory(bind):
        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        sessions.append(session)
        return session

    monkeypatch.setattr(import_logs, "Session", session_factory)
    return sessions


def test_command_fetches_without_a_transaction_and_never_publishes_content(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, db_session: Session, command_sessions: list[Session],
) -> None:
    job = make_job(db_session)
    monkeypatch.setattr(sys, "argv", ["import_logs", "--job-id", str(job.id), "--start-line", "2"])

    def fetch(repository, github_job_id, *, start_line):
        assert (repository, github_job_id, start_line) == ("example/project", 5678, 2)
        assert len(command_sessions) == 1 and not command_sessions[0].in_transaction()
        return process_log(b"setup\nTOKEN=fake-secret\nFAILED", start_line=start_line)

    monkeypatch.setattr(import_logs, "fetch_job_log_excerpt", fetch)
    assert import_logs.main() == 0
    assert len(command_sessions) == 2
    assert all(not session.in_transaction() for session in command_sessions)
    record = db_session.scalar(select(PipelineJobLog))
    assert record.text == "TOKEN=[REDACTED]\nFAILED"
    assert record.start_line == 2 and not record.is_curated
    output = capsys.readouterr().out
    assert "hidden from the public API" in output
    assert "fake-secret" not in output and "FAILED" not in output


@pytest.mark.parametrize("problem", ["missing", "demo", "failed_download"])
def test_prewrite_failures_preserve_existing_evidence(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, db_session: Session,
    command_sessions: list[Session], problem: str,
) -> None:
    job = make_job(db_session)
    save_log_excerpt(db_session, job.id, process_log(b"existing evidence"), is_curated=True)
    if problem == "demo":
        db_session.get(PipelineRun, job.pipeline_run_id).repository = "demo/project"
        db_session.flush()
    monkeypatch.setattr(sys, "argv", ["import_logs", "--job-id", str(2**63 - 1 if problem == "missing" else job.id)])

    def fetch(*args, **kwargs):
        assert problem == "failed_download", "Invalid sources must not contact GitHub"
        raise GitHubError("Log download exceeds the 1 MiB processing limit.")

    monkeypatch.setattr(import_logs, "fetch_job_log_excerpt", fetch)
    assert import_logs.main() == 1
    assert len(command_sessions) == 1
    db_session.expire_all()
    assert db_session.get(PipelineJobLog, job.id).text == "existing evidence"
    assert capsys.readouterr().err


@pytest.mark.parametrize("change", ["deleted_run", "changed_attempt", "changed_job"])
def test_source_identity_is_rechecked_after_download(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, command_sessions: list[Session], change: str,
) -> None:
    job = make_job(db_session)

    def fetch(*args, **kwargs):
        if change == "deleted_run":
            db_session.execute(delete(PipelineRun).where(PipelineRun.id == job.pipeline_run_id))
        elif change == "changed_attempt":
            db_session.get(PipelineRun, job.pipeline_run_id).run_attempt = 2
            db_session.flush()
        else:
            job.github_job_id = 9876
            db_session.flush()
        return process_log(b"downloaded content")

    monkeypatch.setattr(import_logs, "fetch_job_log_excerpt", fetch)
    with pytest.raises(import_logs.LogImportError, match="changed during the fetch"):
        import_logs.import_log(job.id)
    assert db_session.scalar(select(PipelineJobLog)) is None


def test_database_failure_rolls_back_and_hides_internals(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, db_session: Session, command_sessions: list[Session],
) -> None:
    job = make_job(db_session)
    save_log_excerpt(db_session, job.id, process_log(b"previous evidence"))
    monkeypatch.setattr(sys, "argv", ["import_logs", "--job-id", str(job.id)])
    monkeypatch.setattr(import_logs, "fetch_job_log_excerpt", lambda *args, **kwargs: process_log(b"new evidence"))
    save = import_logs.save_log_excerpt

    def fail_after_write(*args, **kwargs):
        save(*args, **kwargs)
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(import_logs, "save_log_excerpt", fail_after_write)
    assert import_logs.main() == 1
    error = capsys.readouterr().err
    assert "Could not store" in error and "private database details" not in error
    db_session.expire_all()
    assert db_session.get(PipelineJobLog, job.id).text == "previous evidence"


@pytest.mark.parametrize("arguments", [[], ["--job-id", "0"], ["--job-id", "abc"], ["--job-id", str(2**63)], ["--job-id", "1", "--start-line", "0"]])
def test_invalid_arguments_never_open_database(monkeypatch: pytest.MonkeyPatch, arguments: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["import_logs", *arguments])
    monkeypatch.setattr(import_logs, "get_engine", lambda: pytest.fail("Invalid arguments must not open a database"))
    with pytest.raises(SystemExit) as error:
        import_logs.main()
    assert error.value.code == 2


def test_explicit_cli_auth_is_in_memory_and_never_printed(monkeypatch, capsys) -> None:
    def credential(command, **options):
        assert command == ["gh", "auth", "token"]
        assert options == {"capture_output": True, "text": True, "timeout": 10, "check": True}
        return subprocess.CompletedProcess(command, 0, stdout="fake-private-token\n", stderr="")
    monkeypatch.setattr(import_logs.subprocess, "run", credential)
    calls = []
    def import_one(job_id, start_line, *, api_token):
        assert api_token.get_secret_value() == "fake-private-token"
        calls.append((job_id, start_line))
    monkeypatch.setattr(import_logs, "import_log", import_one)
    monkeypatch.setattr(sys, "argv", ["import_logs", "--job-id", "5", "--github-cli-auth"])
    assert import_logs.main() == 0 and calls == [(5, 1)]
    captured = capsys.readouterr()
    assert "fake-private-token" not in captured.out + captured.err


@pytest.mark.parametrize("problem", ["missing", "failure", "timeout", "empty"])
def test_cli_credential_failures_are_safe_and_prevent_import(monkeypatch, capsys, problem) -> None:
    def credential(*args, **kwargs):
        if problem == "missing": raise FileNotFoundError("private-command-detail")
        if problem == "failure": raise subprocess.CalledProcessError(1, "gh", stderr="private-token-in-stderr")
        if problem == "timeout": raise subprocess.TimeoutExpired("gh", 10, stderr="private-token-in-stderr")
        return subprocess.CompletedProcess("gh", 0, stdout="\n", stderr="private-token-in-stderr")
    monkeypatch.setattr(import_logs.subprocess, "run", credential)
    monkeypatch.setattr(import_logs, "import_log", lambda *args, **kwargs: pytest.fail("No import without successful local authentication"))
    monkeypatch.setattr(sys, "argv", ["import_logs", "--job-id", "5", "--github-cli-auth"])
    assert import_logs.main() == 1
    captured = capsys.readouterr()
    assert all(secret not in captured.out + captured.err for secret in ("private-command-detail", "private-token-in-stderr"))
