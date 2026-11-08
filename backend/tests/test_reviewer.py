from datetime import datetime, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.draft_reviews import DraftDocument, review_draft, store_draft
from app.investigations import build_report, collect_evidence, evidence_fingerprint
from app.main import app
from app.models import PipelineInvestigationDraft, PipelineJobLog, PipelineRun
from app.seed import seed_demo_evidence, seed_runs

TOKEN = "fake-reviewer-token-" + "x" * 43
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def reviewer_client(runs_client: TestClient):
    settings = Settings(_env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=TOKEN)
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield runs_client
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def private_draft(db_session: Session):
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    run = db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))
    bundle = collect_evidence(db_session, run)
    report = build_report(bundle)
    report.summary = "Private model report marker."
    return store_draft(db_session, DraftDocument(
        generation_method="google-adk-v1", model="gemini-3.5-flash-lite", prompt_version="investigation-v1",
        generated_at=datetime.now(timezone.utc), thinking_level="low", evidence_fingerprint=evidence_fingerprint(bundle),
        evidence_snapshot=bundle, report=report,
    ))


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Basic fake"}, {"Authorization": "Bearer wrong"}, {"Authorization": "Bearer"}])
@pytest.mark.parametrize("path", ["/api/reviewer/drafts", "/api/reviewer/drafts/11111111-1111-1111-1111-111111111111"])
def test_authentication_precedes_database_access_and_never_echoes_token(reviewer_client: TestClient, headers: dict, path: str) -> None:
    def forbidden_session():
        pytest.fail("Unauthenticated request must not acquire a database session")
        yield
    original = app.dependency_overrides[get_session]
    app.dependency_overrides[get_session] = forbidden_session
    try:
        response = reviewer_client.get(path, headers=headers)
    finally:
        app.dependency_overrides[get_session] = original
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["cache-control"] == "no-store" and "Authorization" in response.headers["vary"]
    assert TOKEN not in response.text


def test_disabled_reviewer_access_does_not_affect_public_health(reviewer_client: TestClient) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        _env_file=None, database_url="postgresql+psycopg://user:password@localhost/test", reviewer_access_token=None,
    )
    response = reviewer_client.get("/api/reviewer/drafts", headers=AUTH)
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert reviewer_client.get("/health").json() == {"status": "ok"}


def test_token_in_query_string_is_not_authentication(reviewer_client: TestClient) -> None:
    assert reviewer_client.get("/api/reviewer/drafts", params={"token": TOKEN}).status_code == 401


def test_inbox_is_bounded_filtered_and_excludes_documents(
    reviewer_client: TestClient, db_session: Session, private_draft,
) -> None:
    response = reviewer_client.get("/api/reviewer/drafts?limit=1", headers=AUTH)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert len(response.json()) == 1 and response.json()[0]["id"] == str(private_draft)
    assert response.json()[0]["status"] == "pending_review"
    assert "document" not in response.text and "Private model report marker" not in response.text
    review_draft(db_session, private_draft, "approved", "private-reviewer", "private-note")
    assert reviewer_client.get("/api/reviewer/drafts", headers=AUTH).json() == []
    approved = reviewer_client.get("/api/reviewer/drafts?status=approved", headers=AUTH)
    assert approved.json()[0]["id"] == str(private_draft)


def test_private_detail_validates_evidence_but_never_exposes_snapshot_or_credentials(
    reviewer_client: TestClient, private_draft,
) -> None:
    response = reviewer_client.get(f"/api/reviewer/drafts/{private_draft}", headers=AUTH)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["freshness"] == "current" and body["status"] == "pending_review"
    assert body["report"]["summary"] == "Private model report marker."
    assert body["report"]["findings"][0]["citation"]["source_line"] == 6
    assert all(secret not in response.text for secret in (TOKEN, "evidence_snapshot", "content_fingerprint", "review_note", "demo-only-placeholder"))


@pytest.mark.parametrize("change", ["curation", "text", "document"])
def test_stale_or_invalid_private_report_is_hidden(
    reviewer_client: TestClient, db_session: Session, private_draft, change: str,
) -> None:
    draft = db_session.get(PipelineInvestigationDraft, private_draft)
    if change == "document":
        draft.document = {**draft.document, "prompt_version": "modified"}
    else:
        log = db_session.get(PipelineJobLog, draft.document["evidence_snapshot"]["jobs"][0]["job_id"])
        if change == "curation":
            log.is_curated = False
        else:
            log.text = log.text.replace("503", "502")
    db_session.flush()
    response = reviewer_client.get(f"/api/reviewer/drafts/{private_draft}", headers=AUTH)
    assert response.json()["freshness"] == ("invalid" if change == "document" else "stale")
    assert response.json()["report"] is None
    assert "AssertionError" not in response.text and "Private model report marker" not in response.text


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "limit=bad", "status=unknown"])
def test_private_query_validation_is_not_cached(reviewer_client: TestClient, query: str) -> None:
    response = reviewer_client.get(f"/api/reviewer/drafts?{query}", headers=AUTH)
    assert response.status_code == 422 and response.headers["cache-control"] == "no-store"


def test_missing_and_invalid_ids_and_write_methods(reviewer_client: TestClient) -> None:
    assert reviewer_client.get(f"/api/reviewer/drafts/{uuid4()}", headers=AUTH).status_code == 404
    assert reviewer_client.get("/api/reviewer/drafts/bad", headers=AUTH).status_code == 422
    assert reviewer_client.post("/api/reviewer/drafts", headers=AUTH, json={}).status_code == 405


def test_public_api_never_returns_pending_private_report(reviewer_client: TestClient, db_session: Session, private_draft) -> None:
    draft = db_session.get(PipelineInvestigationDraft, private_draft)
    public = reviewer_client.get(f"/api/runs/{draft.pipeline_run_id}/investigation")
    assert public.status_code == 200 and public.json()["generation_method"] == "rules-v1"
    assert "Private model report marker" not in public.text and str(private_draft) not in public.text
