import sys
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import review_draft as command
from app.draft_reviews import DraftDocument, DraftReviewError, publish_draft, review_draft, store_draft
from app.investigations import build_report, collect_evidence, evidence_fingerprint, save_investigation
from app.models import PipelineInvestigation, PipelineInvestigationDraft, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs


@pytest.fixture
def draft_id(db_session: Session):
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    bundle = collect_evidence(db_session, run)
    report = build_report(bundle)
    # A fictional model result: no provider call or paid dependency is needed.
    report.summary = "The health test received HTTP 503 instead of HTTP 200."
    report.limitations = ["The excerpt does not establish the underlying root cause."]
    return store_draft(db_session, DraftDocument(
        generation_method="google-adk-v1", model="gemini-3.5-flash-lite", prompt_version="investigation-v1",
        generated_at=datetime(2026, 11, 8, 10, tzinfo=timezone.utc), thinking_level="low",
        evidence_fingerprint=evidence_fingerprint(bundle), evidence_snapshot=bundle, report=report,
    ))


def approve(db_session: Session, draft_id) -> None:
    review_draft(db_session, draft_id, "approved", "private-reviewer", "private-review-note")


def test_published_api_exposes_only_the_approved_report_and_is_idempotent(
    db_session: Session, runs_client: TestClient, draft_id,
) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    run_id = draft.pipeline_run_id
    approve(db_session, draft_id)
    # Approval by itself leaves the rule-based public report unchanged.
    assert runs_client.get(f"/api/runs/{run_id}/investigation").json()["generation_method"] == "rules-v1"
    publish_draft(db_session, draft_id)
    payload = runs_client.get(f"/api/runs/{run_id}/investigation").json()
    assert payload["status"] == "ready" and payload["generation_method"] == "google-adk-v1"
    assert payload["report"] == draft.document["report"]
    assert payload["report"]["findings"][0]["citation"]["source_line"] == 6
    public_text = str(payload)
    assert all(private not in public_text for private in (
        "evidence_snapshot", "private-reviewer", "private-review-note", str(draft_id), "prompt_version", "thinking_level",
    ))
    publish_draft(db_session, draft_id)
    assert runs_client.get(f"/api/runs/{run_id}/investigation").json() == payload
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigation)) == 1
    assert db_session.get(PipelineInvestigation, run_id).source_draft_id == draft_id


@pytest.mark.parametrize("status", ["pending_review", "rejected"])
def test_unapproved_drafts_cannot_replace_public_report(db_session: Session, draft_id, status: str) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    before = db_session.get(PipelineInvestigation, draft.pipeline_run_id).report.copy()
    if status == "rejected":
        review_draft(db_session, draft_id, "rejected", "reviewer", "Needs more evidence")
    with pytest.raises(DraftReviewError, match="Only an approved"):
        publish_draft(db_session, draft_id)
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, draft.pipeline_run_id).report == before


@pytest.mark.parametrize("change", ["withdrawn", "log", "payload"])
def test_approval_does_not_authorize_changed_evidence_or_content(db_session: Session, draft_id, change: str) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    approve(db_session, draft_id)
    if change == "payload":
        draft.document = {**draft.document, "prompt_version": "changed-after-approval"}
    else:
        job_id = draft.document["evidence_snapshot"]["jobs"][0]["job_id"]
        log = db_session.get(PipelineJobLog, job_id)
        if change == "withdrawn":
            log.is_curated = False
        else:
            log.text = log.text.replace("503", "502")
    db_session.flush()
    with pytest.raises(DraftReviewError):
        publish_draft(db_session, draft_id)
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, draft.pipeline_run_id).generation_method == "rules-v1"


@pytest.mark.parametrize("change", ["withdrawn", "log", "payload", "approval", "public_report", "source_deleted", "wrong_run"])
def test_public_api_hides_published_content_when_its_basis_changes(
    db_session: Session, runs_client: TestClient, draft_id, change: str,
) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    run_id = draft.pipeline_run_id
    approve(db_session, draft_id)
    publish_draft(db_session, draft_id)
    public = db_session.get(PipelineInvestigation, run_id, populate_existing=True)
    if change in {"withdrawn", "log"}:
        log = db_session.get(PipelineJobLog, draft.document["evidence_snapshot"]["jobs"][0]["job_id"])
        if change == "withdrawn":
            log.is_curated = False
        else:
            log.text = log.text.replace("503", "502")
    elif change == "payload":
        draft.document = {**draft.document, "prompt_version": "changed"}
    elif change == "approval":
        # Simulates a privileged database edit; local review commands cannot do this.
        draft.status = "rejected"
    elif change == "public_report":
        public.report = {**public.report, "summary": "Unapproved replacement"}
    elif change == "wrong_run":
        draft.pipeline_run_id = db_session.scalar(select(PipelineRun.id).where(PipelineRun.github_run_id == 1001))
    else:
        db_session.execute(delete(PipelineInvestigationDraft).where(PipelineInvestigationDraft.id == draft_id))
    db_session.flush()
    payload = runs_client.get(f"/api/runs/{run_id}/investigation").json()
    assert payload == dict(status="stale", generation_method=None, generated_at=None, evidence_fingerprint=None, report=None)


def test_seed_preserves_published_report_and_explicit_rule_generation_clears_provenance(db_session: Session, draft_id) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    approve(db_session, draft_id)
    publish_draft(db_session, draft_id)
    seed_demo_evidence(db_session)
    db_session.expire_all()
    record = db_session.get(PipelineInvestigation, draft.pipeline_run_id)
    assert record.generation_method == "google-adk-v1" and record.source_draft_id == draft_id
    bundle = collect_evidence(db_session, db_session.get(PipelineRun, draft.pipeline_run_id))
    save_investigation(db_session, bundle, build_report(bundle))
    db_session.expire_all()
    assert record.generation_method == "rules-v1" and record.source_draft_id is None


def test_run_deletion_cascades_both_publication_and_source(db_session: Session, draft_id) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    approve(db_session, draft_id)
    publish_draft(db_session, draft_id)
    db_session.execute(delete(PipelineRun).where(PipelineRun.id == draft.pipeline_run_id))
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigation)) == 0
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 0


def test_unknown_draft_is_safe(db_session: Session) -> None:
    with pytest.raises(DraftReviewError, match="not found"):
        publish_draft(db_session, uuid4())


def test_publish_command_commits_only_approved_draft(
    monkeypatch: pytest.MonkeyPatch, db_session: Session, draft_id, capsys: pytest.CaptureFixture,
) -> None:
    draft = db_session.get(PipelineInvestigationDraft, draft_id)
    monkeypatch.setattr(command, "get_engine", lambda: db_session.get_bind())
    monkeypatch.setattr(command, "Session", lambda bind: Session(bind=bind, join_transaction_mode="create_savepoint"))
    monkeypatch.setattr(sys, "argv", ["review_draft", "publish", "--draft-id", str(draft_id)])
    assert command.main() == 1
    assert "Only an approved" in capsys.readouterr().err
    approve(db_session, draft_id)
    assert command.main() == 0
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, draft.pipeline_run_id).source_draft_id == draft_id
    output = capsys.readouterr().out
    assert "Published approved draft" in output and "No model was called" in output
    assert "HTTP 503" not in output and "private-review-note" not in output
