from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.investigations import (
    EvidenceBundle, JobEvidence, build_report, collect_evidence, evidence_fingerprint, save_investigation, validate_citations,
)
from app.log_evidence import save_log_excerpt
from app.log_processing import process_log
from app.models import PipelineInvestigation, PipelineJob, PipelineJobLog, PipelineRun
from app.schemas import LogLineResponse
from app.seed import seed_demo_evidence, seed_runs


def bundle_with_lines(*texts: str) -> EvidenceBundle:
    return EvidenceBundle(
        run_id=1, repository="demo/project", github_run_id=1002, run_attempt=1,
        status="completed", conclusion="failure", failed_job_count=1,
        jobs=[JobEvidence(
            job_id=2, job_name="Tests", github_job_id=2001, status="completed", conclusion="failure",
            lines=[LogLineResponse(source_line=index + 5, text=text) for index, text in enumerate(texts)],
            total_source_lines=20, truncated=True, source_kind="demo", log_updated_at="2026-11-07T12:00:00+00:00",
        )],
    )


@pytest.mark.parametrize("texts, title, observation", [
    (("FAILED test_health", "AssertionError: expected HTTP 200, received HTTP 503"), "HTTP status assertion failed", "A test expected HTTP 200 but received HTTP 503."),
    (("Operation timed out after 30 seconds",), "Timeout reported", "The cited log line reports a timeout."),
    (("Error: build failed",), "Failure diagnostic recorded", "The cited excerpt contains a failure diagnostic."),
])
def test_report_describes_diagnostics_with_exact_citations(texts: tuple, title: str, observation: str) -> None:
    bundle = bundle_with_lines(*texts)
    report = build_report(bundle)
    finding = report.findings[0]
    assert finding.title == title and finding.observation == observation
    validate_citations(report, bundle)
    assert finding.citation.quote in texts
    assert any("does not establish" in limitation for limitation in report.limitations)
    assert any("No AI model" in limitation for limitation in report.limitations)
    assert any("omitted" in limitation for limitation in report.limitations)


@pytest.mark.parametrize("texts", [(), ("Everything looks ordinary",), ("Error: " + "x" * 4096,)])
def test_insufficient_evidence_never_fabricates_findings(texts: tuple) -> None:
    report = build_report(bundle_with_lines(*texts))
    assert report.findings == []
    assert report.summary.startswith("Insufficient curated evidence")


@pytest.mark.parametrize("change", [{"job_id": 999}, {"source_line": 99}, {"quote": "invented evidence"}, {"job_name": "another job"}])
def test_invented_citations_are_rejected(change: dict) -> None:
    bundle = bundle_with_lines("Error: actual failure")
    report = build_report(bundle)
    report.findings[0].citation = report.findings[0].citation.model_copy(update=change)
    with pytest.raises(ValueError, match="does not match"):
        validate_citations(report, bundle)


def test_fingerprint_is_stable_and_sensitive_to_source_identity_and_content() -> None:
    bundle = bundle_with_lines("Error: actual failure")
    original = evidence_fingerprint(bundle)
    assert original == evidence_fingerprint(EvidenceBundle.model_validate(bundle.model_dump()))
    assert original != evidence_fingerprint(bundle.model_copy(update={"run_attempt": 2}))
    changed = bundle.model_copy(deep=True)
    changed.jobs[0].lines[0].text = "Error: different failure"
    assert original != evidence_fingerprint(changed)


def test_no_recorded_failures_are_not_reported_as_a_failure() -> None:
    bundle = bundle_with_lines().model_copy(update={"failed_job_count": 0, "jobs": []})
    report = build_report(bundle)
    assert report.summary == "No failed jobs or steps are recorded for this attempt."
    assert report.findings == []


@pytest.fixture
def investigated_run(db_session: Session) -> PipelineRun:
    seed_runs(db_session)
    seed_demo_evidence(db_session)
    return db_session.scalar(select(PipelineRun).where(PipelineRun.github_run_id == 1002))


def test_ready_api_returns_validated_report_without_internal_snapshot(
    runs_client: TestClient, db_session: Session, investigated_run: PipelineRun,
) -> None:
    response = runs_client.get(f"/api/runs/{investigated_run.id}/investigation")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["status"] == "ready" and payload["generation_method"] == "rules-v1"
    assert "evidence_snapshot" not in payload
    assert payload["report"]["findings"][0]["citation"]["source_line"] == 6
    assert payload["report"]["findings"][0]["citation"]["quote"] == "AssertionError: expected HTTP 200, received HTTP 503"
    assert "demo-only-placeholder" not in response.text
    assert db_session.get(PipelineInvestigation, investigated_run.id).evidence_snapshot["jobs"]


@pytest.mark.parametrize("change", ["withdrawn", "log_changed", "job_renamed", "attempt_changed", "job_deleted"])
def test_changed_or_withdrawn_evidence_hides_all_previous_report_content(
    runs_client: TestClient, db_session: Session, investigated_run: PipelineRun, change: str,
) -> None:
    log = db_session.scalar(select(PipelineJobLog))
    job = db_session.get(PipelineJob, log.pipeline_job_id)
    if change == "withdrawn":
        log.is_curated = False
    elif change == "log_changed":
        log.text = log.text.replace("HTTP 503", "HTTP 500")
    elif change == "job_renamed":
        job.name = "Changed job"
    elif change == "attempt_changed":
        investigated_run.run_attempt = 2
    else:
        db_session.execute(delete(PipelineJob).where(PipelineJob.id == job.id))
    db_session.flush()
    payload = runs_client.get(f"/api/runs/{investigated_run.id}/investigation").json()
    assert payload == dict(status="stale", generation_method=None, generated_at=None, evidence_fingerprint=None, report=None)


def test_evidence_never_includes_uncurated_or_other_attempt_logs(db_session: Session, investigated_run: PipelineRun) -> None:
    log = db_session.scalar(select(PipelineJobLog))
    log.is_curated = False
    other = PipelineRun(
        repository=investigated_run.repository, github_run_id=1002, run_attempt=2,
        workflow_name="Other attempt", status="completed", conclusion="failure", created_at=investigated_run.created_at,
    )
    db_session.add(other)
    db_session.flush()
    job = PipelineJob(pipeline_run_id=other.id, github_job_id=3001, name="Other job", status="completed", conclusion="failure")
    db_session.add(job)
    db_session.flush()
    save_log_excerpt(db_session, job.id, process_log(b"Error: other attempt evidence"), source_kind="demo", is_curated=True)
    evidence = collect_evidence(db_session, investigated_run)
    assert evidence.failed_job_count == 1 and evidence.jobs == []
    report = build_report(evidence)
    assert not report.findings
    assert "other attempt evidence" not in report.model_dump_json()


def test_successful_job_with_failed_step_remains_eligible(db_session: Session, investigated_run: PipelineRun) -> None:
    db_session.scalar(select(PipelineJob)).conclusion = "success"
    db_session.flush()
    bundle = collect_evidence(db_session, investigated_run)
    assert bundle.failed_job_count == 1 and len(bundle.jobs) == 1


def test_evidence_job_cap_is_explicit_and_deterministic(db_session: Session, investigated_run: PipelineRun) -> None:
    for index in range(11):
        job = PipelineJob(
            pipeline_run_id=investigated_run.id, github_job_id=3000 + index,
            name=f"Failed job {index}", status="completed", conclusion="failure",
        )
        db_session.add(job)
        db_session.flush()
        save_log_excerpt(db_session, job.id, process_log(b"Error: synthetic failure"), is_curated=True)
    bundle = collect_evidence(db_session, investigated_run)
    assert bundle.failed_job_count == 12 and len(bundle.jobs) == 10
    assert [job.github_job_id for job in bundle.jobs] == [2001, *range(3000, 3009)]
    assert any("2 failed jobs were excluded" in limitation for limitation in build_report(bundle).limitations)


def test_identical_generation_is_idempotent_and_replacement_updates_timestamp(db_session: Session, investigated_run: PipelineRun) -> None:
    record = db_session.get(PipelineInvestigation, investigated_run.id)
    original_time = datetime(2000, 1, 1, tzinfo=timezone.utc)
    record.generated_at = original_time
    db_session.flush()
    bundle = collect_evidence(db_session, investigated_run)
    save_investigation(db_session, bundle, build_report(bundle))
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, investigated_run.id).generated_at == original_time
    log = db_session.scalar(select(PipelineJobLog))
    log.text = log.text.replace("HTTP 503", "HTTP 500")
    db_session.flush()
    bundle = collect_evidence(db_session, investigated_run)
    save_investigation(db_session, bundle, build_report(bundle))
    db_session.expire_all()
    assert db_session.get(PipelineInvestigation, investigated_run.id).generated_at > original_time
    assert db_session.scalar(select(func.count()).select_from(PipelineInvestigation)) == 1


def test_missing_report_and_missing_run_have_distinct_responses(runs_client: TestClient, db_session: Session) -> None:
    seed_runs(db_session)
    run_id = db_session.scalar(select(PipelineRun.id))
    assert runs_client.get(f"/api/runs/{run_id}/investigation").json()["status"] == "not_generated"
    assert runs_client.get("/api/runs/9223372036854775807/investigation").status_code == 404


@pytest.mark.parametrize("run_id", ["0", "-1", "abc", "9223372036854775808"])
def test_invalid_parent_id_is_rejected(runs_client: TestClient, run_id: str) -> None:
    assert runs_client.get(f"/api/runs/{run_id}/investigation").status_code == 422


def test_deleting_run_cascades_report(db_session: Session, investigated_run: PipelineRun) -> None:
    db_session.execute(delete(PipelineRun).where(PipelineRun.id == investigated_run.id))
    assert db_session.scalar(select(PipelineInvestigation)) is None
