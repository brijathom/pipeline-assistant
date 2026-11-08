import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import review_draft as command
from app.draft_reviews import (
    MAX_DRAFT_BYTES, DraftDocument, DraftReviewError, load_draft, review_draft, store_draft,
)
from app.investigations import build_report, collect_evidence, evidence_fingerprint
from app.models import PipelineInvestigation, PipelineInvestigationDraft, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


@pytest.fixture
def document(db_session: Session) -> DraftDocument:
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    bundle = collect_evidence(db_session, run)
    return DraftDocument(
        generation_method="google-adk-v1", model="gemini-3.5-flash-lite", prompt_version="investigation-v1",
        generated_at=datetime.now(timezone.utc), thinking_level="low", evidence_fingerprint=evidence_fingerprint(bundle),
        evidence_snapshot=bundle, report=build_report(bundle),
    )


def test_import_is_private_and_reimport_preserves_a_final_decision(
    db_session: Session, document: DraftDocument, runs_client: TestClient,
) -> None:
    before = db_session.get(PipelineInvestigation, document.evidence_snapshot.run_id).report.copy()
    draft_id = store_draft(db_session, document)
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    assert draft.status == "pending_review" and draft.reviewed_at is None
    assert draft.document["report"] == document.report.model_dump(mode="json")
    assert "status" not in draft.document
    review_draft(db_session, draft_id, "approved", " local-reviewer ", " Checked source line 6. ")
    reviewed_at = draft.reviewed_at
    assert store_draft(db_session, document) == draft_id
    db_session.expire_all()
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    assert draft.status == "approved" and draft.reviewed_at == reviewed_at
    assert draft.reviewed_by == "local-reviewer" and draft.review_note == "Checked source line 6."
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 1
    assert db_session.get(PipelineInvestigation, document.evidence_snapshot.run_id).report == before
    response = runs_client.get(f"/api/runs/{document.evidence_snapshot.run_id}/investigation")
    assert response.status_code == 200
    assert response.json()["generation_method"] == "rules-v1"
    assert str(draft_id) not in response.text


@pytest.mark.parametrize("change", ["text", "curation", "run", "fingerprint", "citation"])
def test_invalid_or_stale_import_writes_nothing(db_session: Session, document: DraftDocument, change: str) -> None:
    if change in {"text", "curation"}:
        log = db_session.get(PipelineJobLog, document.evidence_snapshot.jobs[0].job_id)
        if change == "text":
            log.text = log.text.replace("503", "502")
        else:
            log.is_curated = False
    elif change == "run":
        document.evidence_snapshot.run_id = 9_000_000_000
        document.evidence_fingerprint = evidence_fingerprint(document.evidence_snapshot)
    elif change == "fingerprint":
        document.evidence_fingerprint = "0" * 64
    else:
        document.report.findings[0].citation.quote = "invented"
    with pytest.raises((DraftReviewError, ValueError)):
        store_draft(db_session, document)
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 0


def test_stale_approval_is_blocked_but_rejection_is_allowed(db_session: Session, document: DraftDocument) -> None:
    draft_id = store_draft(db_session, document)
    db_session.get(PipelineJobLog, document.evidence_snapshot.jobs[0].job_id).is_curated = False
    with pytest.raises(DraftReviewError, match="Evidence changed"):
        review_draft(db_session, draft_id, "approved", "reviewer", "Read the report")
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    assert draft.status == "pending_review" and draft.reviewed_at is None
    review_draft(db_session, draft_id, "rejected", "reviewer", "Evidence was withdrawn")
    assert draft.status == "rejected" and draft.reviewed_at is not None


@pytest.mark.parametrize("decision", ["approved", "rejected"])
def test_final_review_cannot_be_overwritten_even_with_cached_pending_state(
    db_session: Session, document: DraftDocument, decision: str,
) -> None:
    draft_id = store_draft(db_session, document)
    cached = db_session.get(PipelineInvestigationDraft, draft_id)
    # Simulate another reviewer having committed a decision while this Session
    # still holds its old pending object. The service must refresh under lock.
    db_session.execute(update(PipelineInvestigationDraft).where(PipelineInvestigationDraft.id == draft_id).values(
        status=decision, reviewed_at=func.now(), reviewed_by="first", review_note="Original decision",
    ).execution_options(synchronize_session=False))
    assert cached.status == "pending_review"
    with pytest.raises(DraftReviewError, match="already been reviewed"):
        review_draft(db_session, draft_id, "rejected", "second", "Overwrite attempt")
    assert cached.status == decision and cached.reviewed_by == "first"


def test_modified_stored_payload_cannot_be_approved(db_session: Session, document: DraftDocument) -> None:
    draft_id = store_draft(db_session, document)
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    payload = {**draft.document, "prompt_version": "modified"}
    draft.document = payload
    db_session.flush()
    with pytest.raises(DraftReviewError, match="integrity"):
        review_draft(db_session, draft_id, "approved", "reviewer", "Read it")
    assert draft.status == "pending_review"


@pytest.mark.parametrize("reviewer,note,decision", [(" ", "ok", "approved"), ("a" * 101, "ok", "approved"), ("user", " ", "approved"), ("user", "x" * 1001, "approved"), ("user", "ok", "pending_review")])
def test_invalid_review_fields_change_nothing(
    db_session: Session, document: DraftDocument, reviewer: str, note: str, decision: str,
) -> None:
    draft_id = store_draft(db_session, document)
    with pytest.raises(DraftReviewError):
        review_draft(db_session, draft_id, decision, reviewer, note)
    assert db_session.get(PipelineInvestigationDraft, draft_id).status == "pending_review"


def test_unknown_draft_is_safe(db_session: Session) -> None:
    with pytest.raises(DraftReviewError, match="not found"):
        review_draft(db_session, uuid4(), "rejected", "reviewer", "Not present")


@pytest.mark.parametrize("values", [
    {"status": "approved"},
    {"status": "pending_review", "reviewed_by": "reviewer"},
    {"status": "other"},
])
def test_database_rejects_incomplete_or_invalid_decisions(db_session: Session, document: DraftDocument, values: dict) -> None:
    draft_id = store_draft(db_session, document)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.execute(update(PipelineInvestigationDraft).where(PipelineInvestigationDraft.id == draft_id).values(**values))
    assert db_session.get(PipelineInvestigationDraft, draft_id).status == "pending_review"


def test_file_size_and_schema_are_bounded_before_import(tmp_path: Path) -> None:
    path = tmp_path / "draft.json"
    path.write_bytes(b"x" * (MAX_DRAFT_BYTES + 1))
    with pytest.raises(DraftReviewError, match="1 MiB"):
        load_draft(path)
    path.write_text('{"status": "approved", "private-text": "never echo"}', encoding="utf-8")
    with pytest.raises(ValidationError):
        load_draft(path)


def test_local_command_import_and_reject_without_printing_private_report(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, document: DraftDocument, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    path = tmp_path / "draft.json"
    path.write_text(document.model_dump_json(), encoding="utf-8")
    monkeypatch.setattr(command, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(command, "Session", lambda bind: Session(bind=bind, join_transaction_mode="create_savepoint"))
    monkeypatch.setattr(sys, "argv", ["review_draft", "import", "--path", str(path)])
    assert command.main() == 0
    draft_id = db_session.scalar(select(PipelineInvestigationDraft.id))
    monkeypatch.setattr(sys, "argv", ["review_draft", "review", "--draft-id", str(draft_id), "--decision", "rejected", "--reviewer", "local", "--note", "Fixture review"])
    assert command.main() == 0
    db_session.expire_all()
    assert db_session.get(PipelineInvestigationDraft, draft_id).status == "rejected"
    output = capsys.readouterr().out
    assert "public report was not changed" in output and "No model was called" in output
    assert "AssertionError" not in output and "HTTP 503" not in output


def test_invalid_file_is_not_echoed_and_never_opens_database(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture,
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text('{"provider-output": "private-key-and-response"}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["review_draft", "import", "--path", str(path)])
    monkeypatch.setattr(command, "get_engine", lambda: pytest.fail("Must validate file first"))
    assert command.main() == 1
    assert "private-key-and-response" not in capsys.readouterr().err


def test_bad_uuid_fails_before_database_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["review_draft", "review", "--draft-id", "invalid", "--decision", "approved", "--reviewer", "local", "--note", "check"])
    monkeypatch.setattr(command, "get_engine", lambda: pytest.fail("Arguments must be validated first"))
    with pytest.raises(SystemExit) as error:
        command.main()
    assert error.value.code == 2
