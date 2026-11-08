import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import draft_investigation
from app.ai_investigation import AIGenerationError
from app.investigate_run import InvestigationError
from app.investigations import build_report
from app.models import PipelineInvestigation, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


@pytest.fixture
def draft_setup(monkeypatch: pytest.MonkeyPatch, db_session: Session, tmp_path: Path) -> tuple[int, list[Session]]:
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run_id = db_session.scalar(select(PipelineRun.id).where(PipelineRun.github_run_id == 1002))
    sessions = []
    monkeypatch.setattr(draft_investigation, "get_settings", lambda: SimpleNamespace(gemini_api_key=SecretStr("fake-api-key"), ai_model="gemini-3.8-flash"))
    monkeypatch.setattr(draft_investigation, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(draft_investigation, "DRAFT_DIRECTORY", tmp_path / ".drafts")
    monkeypatch.setattr(sys, "argv", ["draft_investigation", "--run-id", str(run_id)])

    def session_factory(bind):
        session = Session(bind=bind, join_transaction_mode="create_savepoint")
        sessions.append(session)
        return session

    monkeypatch.setattr(draft_investigation, "Session", session_factory)
    return run_id, sessions


def test_private_draft_does_not_replace_public_report_or_hold_database_connection(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, draft_setup: tuple,
) -> None:
    run_id, sessions = draft_setup
    before = db_session.get(PipelineInvestigation, run_id).report

    async def generate(bundle, **kwargs):
        assert len(sessions) == 1 and not sessions[0].in_transaction()
        assert kwargs["api_key"].get_secret_value() == "fake-api-key"
        return build_report(bundle)

    monkeypatch.setattr(draft_investigation, "generate_ai_report", generate)
    path = draft_investigation.draft_investigation(run_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["status"] == "pending_review"
    assert document["generation_method"] == "google-adk-v1"
    assert document["model"] == "gemini-3.8-flash" and document["prompt_version"]
    assert document["report"]["findings"][0]["citation"]["source_line"] == 6
    assert "fake-api-key" not in path.read_text(encoding="utf-8")
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, run_id).report == before
    assert len(sessions) == 2 and all(not session.in_transaction() for session in sessions)


@pytest.mark.parametrize("problem", ["provider", "withdrawn", "invalid_citation"])
def test_failure_or_source_change_creates_no_draft(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, draft_setup: tuple, problem: str,
) -> None:
    run_id, _ = draft_setup

    async def generate(bundle, **kwargs):
        if problem == "provider":
            raise AIGenerationError("Generation failed. No draft was saved.")
        report = build_report(bundle)
        if problem == "withdrawn":
            db_session.scalar(select(PipelineJobLog)).is_curated = False
            db_session.flush()
        else:
            report.findings[0].citation.quote = "invented evidence"
        return report

    monkeypatch.setattr(draft_investigation, "generate_ai_report", generate)
    with pytest.raises((AIGenerationError, InvestigationError, ValueError)):
        draft_investigation.draft_investigation(run_id)
    assert not draft_investigation.DRAFT_DIRECTORY.exists()


def test_missing_credentials_prevent_database_and_provider_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(draft_investigation, "get_settings", lambda: SimpleNamespace(gemini_api_key=None))
    monkeypatch.setattr(draft_investigation, "get_engine", lambda: pytest.fail("Must not open database"))
    with pytest.raises(AIGenerationError, match="GEMINI_API_KEY"):
        draft_investigation.draft_investigation(1)


def test_command_prints_only_draft_path_and_review_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture, draft_setup: tuple,
) -> None:
    async def generate(bundle, **kwargs):
        return build_report(bundle)

    monkeypatch.setattr(draft_investigation, "generate_ai_report", generate)
    assert draft_investigation.main() == 0
    output = capsys.readouterr().out
    assert "unreviewed AI draft" in output and "public report was not changed" in output
    assert "HTTP 503" not in output and "fake-api-key" not in output


@pytest.mark.parametrize("args", [[], ["--run-id", "0"], ["--run-id", "abc"], ["--run-id", str(2**63)]])
def test_bad_arguments_never_load_settings(monkeypatch: pytest.MonkeyPatch, args: list[str]) -> None:
    monkeypatch.setattr(sys, "argv", ["draft_investigation", *args])
    monkeypatch.setattr(draft_investigation, "get_settings", lambda: pytest.fail("Must validate arguments first"))
    with pytest.raises(SystemExit) as error:
        draft_investigation.main()
    assert error.value.code == 2
