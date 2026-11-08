import sys

import pytest
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app import investigate_run
from app.models import PipelineInvestigation, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


@pytest.fixture
def command_setup(monkeypatch: pytest.MonkeyPatch, db_session: Session) -> tuple[int, list[Session]]:
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run_id = db_session.scalar(select(PipelineRun.id).where(PipelineRun.github_run_id == 1002))
    sessions = []
    monkeypatch.setattr(investigate_run, "get_engine", lambda: db_session.get_bind())

    def session_factory(bind):
        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        sessions.append(session)
        return session

    monkeypatch.setattr(investigate_run, "Session", session_factory)
    monkeypatch.setattr(sys, "argv", ["investigate_run", "--run-id", str(run_id)])
    return run_id, sessions


def test_generation_happens_outside_transaction_and_prints_no_quotes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, command_setup: tuple,
) -> None:
    _, sessions = command_setup
    build = investigate_run.build_report

    def generate(bundle):
        assert len(sessions) == 1 and not sessions[0].in_transaction()
        return build(bundle)

    monkeypatch.setattr(investigate_run, "build_report", generate)
    assert investigate_run.main() == 0
    output = capsys.readouterr().out
    assert "No AI model was called" in output and "HTTP 503" not in output
    assert len(sessions) == 2 and all(not session.in_transaction() for session in sessions)


def test_evidence_change_during_generation_preserves_previous_report(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, command_setup: tuple,
) -> None:
    run_id, _ = command_setup
    original = db_session.get(PipelineInvestigation, run_id).evidence_fingerprint
    build = investigate_run.build_report

    def generate(bundle):
        db_session.scalar(select(PipelineJobLog)).is_curated = False
        db_session.flush()
        return build(bundle)

    monkeypatch.setattr(investigate_run, "build_report", generate)
    with pytest.raises(investigate_run.InvestigationError, match="changed during generation"):
        investigate_run.investigate_run(run_id)
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, run_id).evidence_fingerprint == original


def test_database_failure_hides_details_and_preserves_report(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, db_session: Session, command_setup: tuple,
) -> None:
    run_id, _ = command_setup
    original = db_session.get(PipelineInvestigation, run_id).report
    log = db_session.scalar(select(PipelineJobLog))
    log.text = log.text.replace("HTTP 503", "HTTP 500")
    db_session.flush()
    save = investigate_run.save_investigation

    def fail(*args):
        save(*args)
        raise SQLAlchemyError("private database details")

    monkeypatch.setattr(investigate_run, "save_investigation", fail)
    assert investigate_run.main() == 1
    assert "private database details" not in capsys.readouterr().err
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, run_id).report == original


@pytest.mark.parametrize("args", [[], ["--run-id", "0"], ["--run-id", "abc"], ["--run-id", str(2**63)]])
def test_invalid_arguments_do_not_open_database(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["investigate_run", *args])
    monkeypatch.setattr(investigate_run, "get_engine", lambda: pytest.fail("Must not open database"))
    with pytest.raises(SystemExit) as error:
        investigate_run.main()
    assert error.value.code == 2
