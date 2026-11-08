"""Curated evidence snapshots and deterministic reports; no model or network calls."""
import hashlib
import json
import re

from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models import PipelineInvestigation, PipelineJob, PipelineJobLog, PipelineJobStep, PipelineRun
from app.schemas import (
    InvestigationCitation, InvestigationFinding, InvestigationReport, LogLineResponse, PipelineInvestigationResponse,
)

GENERATION_METHOD = "rules-v1"
MAX_EVIDENCE_JOBS = 10
_DIAGNOSTIC = re.compile(r"\b(?:AssertionError|Error|Exception|FAILED|timed out)\b", re.IGNORECASE)
_HTTP_ASSERTION = re.compile(r"expected HTTP (\d{3}), received HTTP (\d{3})", re.IGNORECASE)


class JobEvidence(BaseModel):
    job_id: int
    job_name: str = Field(max_length=240)
    github_job_id: int
    status: str
    conclusion: str | None
    lines: list[LogLineResponse] = Field(max_length=100)
    total_source_lines: int
    truncated: bool
    source_kind: str
    log_updated_at: str


class EvidenceBundle(BaseModel):
    run_id: int
    repository: str
    github_run_id: int
    run_attempt: int
    status: str
    conclusion: str | None
    failed_job_count: int
    jobs: list[JobEvidence] = Field(max_length=MAX_EVIDENCE_JOBS)


def collect_evidence(session: Session, run: PipelineRun) -> EvidenceBundle:
    """Use only curated excerpts from this attempt, with a stable ten-job cap."""
    failed = or_(
        PipelineJob.conclusion.in_(("failure", "timed_out")),
        select(PipelineJobStep.id).where(
            PipelineJobStep.pipeline_job_id == PipelineJob.id,
            PipelineJobStep.conclusion.in_(("failure", "timed_out")),
        ).exists(),
    )
    count = session.scalar(select(func.count()).select_from(PipelineJob).where(
        PipelineJob.pipeline_run_id == run.id, failed,
    ))
    rows = session.execute(select(PipelineJob, PipelineJobLog).join(
        PipelineJobLog, PipelineJobLog.pipeline_job_id == PipelineJob.id,
    ).where(
        PipelineJob.pipeline_run_id == run.id, failed, PipelineJobLog.is_curated.is_(True),
    ).order_by(PipelineJob.github_job_id, PipelineJob.id).limit(MAX_EVIDENCE_JOBS))
    jobs = []
    for job, log in rows:
        texts = log.text.split("\n") if log.line_count else []
        jobs.append(JobEvidence(
            job_id=job.id, job_name=job.name[:240], github_job_id=job.github_job_id,
            status=job.status, conclusion=job.conclusion,
            lines=[LogLineResponse(source_line=log.start_line + index, text=text) for index, text in enumerate(texts)],
            total_source_lines=log.total_source_lines, truncated=log.truncated,
            source_kind=log.source_kind, log_updated_at=log.updated_at.isoformat(),
        ))
    return EvidenceBundle(
        run_id=run.id, repository=run.repository, github_run_id=run.github_run_id, run_attempt=run.run_attempt,
        status=run.status, conclusion=run.conclusion, failed_job_count=count, jobs=jobs,
    )


def evidence_fingerprint(bundle: EvidenceBundle) -> str:
    encoded = json.dumps(bundle.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_report(bundle: EvidenceBundle) -> InvestigationReport:
    """Describe observed diagnostics without claiming to establish a root cause."""
    findings = []
    limitations = [
        "This rule-based report uses stored curated evidence. No AI model was called.",
        "A diagnostic identifies an observed failure; it does not establish the underlying root cause.",
    ]
    for job in bundle.jobs:
        candidates = [line for line in job.lines if len(line.text) <= 4096 and _DIAGNOSTIC.search(line.text)]
        line = next((line for line in candidates if _HTTP_ASSERTION.search(line.text)), candidates[0] if candidates else None)
        if line is None:
            limitations.append(f"Job {job.job_id} has no recognized diagnostic within the available excerpt and quote limit.")
            continue
        assertion = _HTTP_ASSERTION.search(line.text)
        if assertion:
            title = "HTTP status assertion failed"
            observation = f"A test expected HTTP {assertion[1]} but received HTTP {assertion[2]}."
            recommendation = (
                f"Reproduce the failing test and check why the tested endpoint returned HTTP {assertion[2]}, "
                "including any dependency-health checks. Confirm the cause before changing retries."
            )
        elif "timed out" in line.text.lower():
            title = "Timeout reported"
            observation = "The cited log line reports a timeout."
            recommendation = "Identify the operation that timed out and inspect its duration and dependencies before changing timeout settings."
        else:
            title = "Failure diagnostic recorded"
            observation = "The cited excerpt contains a failure diagnostic."
            recommendation = "Inspect the cited diagnostic and reproduce the failing job locally before changing configuration or adding retries."
        findings.append(InvestigationFinding(
            title=title, observation=observation, recommendation=recommendation,
            citation=InvestigationCitation(job_id=job.job_id, job_name=job.job_name, source_line=line.source_line, quote=line.text),
        ))
    omitted = bundle.failed_job_count - len(bundle.jobs)
    if omitted:
        limitations.append(f"{omitted} failed jobs were excluded because curated excerpts were unavailable or the ten-job limit was reached.")
    if any(job.truncated for job in bundle.jobs):
        limitations.append("Some source log content was omitted from the stored excerpts.")
    if findings:
        summary = f"Found {len(findings)} supported failure diagnostics in curated job excerpts."
    elif bundle.failed_job_count == 0:
        summary = "No failed jobs or steps are recorded for this attempt."
    else:
        summary = "Insufficient curated evidence to identify a failure diagnostic."
    return InvestigationReport(summary=summary, findings=findings, limitations=limitations)


def validate_citations(report: InvestigationReport, bundle: EvidenceBundle) -> None:
    """Reject invented jobs, source lines, or quotes before persisting a report."""
    jobs = {job.job_id: job for job in bundle.jobs}
    for finding in report.findings:
        citation = finding.citation
        job = jobs.get(citation.job_id)
        if job is None or citation.job_name != job.job_name or not any(
            line.source_line == citation.source_line and line.text == citation.quote for line in job.lines
        ):
            raise ValueError("Investigation citation does not match its evidence snapshot.")


def save_investigation(session: Session, bundle: EvidenceBundle, report: InvestigationReport) -> None:
    """Keep one current report per attempt; identical evidence is idempotent."""
    validate_citations(report, bundle)
    statement = insert(PipelineInvestigation).values(
        pipeline_run_id=bundle.run_id, generation_method=GENERATION_METHOD,
        evidence_fingerprint=evidence_fingerprint(bundle), evidence_snapshot=bundle.model_dump(mode="json"),
        report=report.model_dump(mode="json"),
    )
    session.execute(statement.on_conflict_do_update(
        index_elements=[PipelineInvestigation.pipeline_run_id],
        set_={**{key: getattr(statement.excluded, key) for key in (
            "generation_method", "evidence_fingerprint", "evidence_snapshot", "report",
        )}, "generated_at": func.now()},
        where=or_(
            PipelineInvestigation.evidence_fingerprint != statement.excluded.evidence_fingerprint,
            PipelineInvestigation.generation_method != statement.excluded.generation_method,
        ),
    ))


def find_investigation(session: Session, run: PipelineRun) -> PipelineInvestigationResponse:
    record = session.get(PipelineInvestigation, run.id)
    if record is None:
        return PipelineInvestigationResponse(status="not_generated")
    if record.evidence_fingerprint != evidence_fingerprint(collect_evidence(session, run)):
        # Never send old quotes after source evidence changes or loses curation.
        return PipelineInvestigationResponse(status="stale")
    return PipelineInvestigationResponse(
        status="ready", generation_method=record.generation_method, generated_at=record.generated_at,
        evidence_fingerprint=record.evidence_fingerprint, report=InvestigationReport.model_validate(record.report),
    )
