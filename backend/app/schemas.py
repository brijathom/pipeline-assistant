from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool


class PipelineRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repository: str
    github_run_id: int
    run_attempt: int
    workflow_name: str
    status: str
    conclusion: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class PipelineMetricsResponse(BaseModel):
    repository: str | None
    total_runs: int
    successful_runs: int
    failed_runs: int
    in_progress_runs: int


class JobDurationSummary(BaseModel):
    total_jobs: int
    timed_jobs: int
    excluded_jobs: int
    median_job_duration_seconds: float | None
    p95_job_duration_seconds: float | None


class JobDurationDay(JobDurationSummary):
    date: date


class PipelinePerformanceResponse(JobDurationSummary):
    repository: str | None
    days: Literal[7, 30, 90]
    start_at: datetime
    end_at: datetime
    daily: list[JobDurationDay]


class PipelineJobStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step_number: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None


class PipelineJobResponse(BaseModel):
    id: int
    github_job_id: int
    name: str
    status: str
    conclusion: str | None
    started_at: datetime | None
    completed_at: datetime | None
    steps: list[PipelineJobStepResponse]


class LogLineResponse(BaseModel):
    source_line: int
    text: str


class PipelineJobLogResponse(BaseModel):
    lines: list[LogLineResponse]
    total_source_lines: int
    truncated: bool
    redactions_applied: bool
    source_kind: Literal["github", "demo"]
    updated_at: datetime


class InvestigationCitation(BaseModel):
    # Inclusive integer bounds also work with Gemini's response-schema subset.
    job_id: int = Field(ge=1)
    job_name: str = Field(min_length=1, max_length=240)
    source_line: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=4096)


class InvestigationFinding(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    observation: str = Field(min_length=1, max_length=1000)
    recommendation: str = Field(min_length=1, max_length=1000)
    citation: InvestigationCitation


class InvestigationReport(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    schema_version: Literal[1] = 1
    summary: str = Field(min_length=1, max_length=1000)
    findings: list[InvestigationFinding] = Field(max_length=10)
    limitations: list[str] = Field(max_length=15)


class PipelineInvestigationResponse(BaseModel):
    status: Literal["ready", "stale", "not_generated"]
    generation_method: Literal["rules-v1", "google-adk-v1"] | None = None
    generated_at: datetime | None = None
    evidence_fingerprint: str | None = None
    report: InvestigationReport | None = None


class ReviewerDraftSummary(BaseModel):
    id: UUID
    pipeline_run_id: int
    repository: str
    github_run_id: int
    run_attempt: int
    status: Literal["pending_review", "approved", "rejected"]
    created_at: datetime


class ReviewerDraftDetails(ReviewerDraftSummary):
    freshness: Literal["current", "stale", "invalid"]
    is_published: bool = False
    model: str | None = None
    generated_at: datetime | None = None
    report: InvestigationReport | None = None
    reviewed_at: datetime | None = None
    reviewed_by: str | None = None
    review_note: str | None = None


class ReviewerDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    decision: Literal["approved", "rejected"]
    note: str = Field(min_length=1, max_length=1000, pattern=r"^[^\x00]+$")


class ReviewerLogSummary(BaseModel):
    job_id: int
    pipeline_run_id: int
    repository: str
    github_run_id: int
    run_attempt: int
    job_name: str
    is_curated: bool
    updated_at: datetime


class ReviewerLogDetails(ReviewerLogSummary):
    revision: str
    excerpt: PipelineJobLogResponse


class LogCurationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    expected_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    is_curated: StrictBool


class InvestigationTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    request_id: UUID
    run_id: int = Field(gt=0, le=9_223_372_036_854_775_807)


class InvestigationTaskResponse(BaseModel):
    id: UUID
    pipeline_run_id: int
    repository: str
    github_run_id: int
    run_attempt: int
    status: Literal["queued", "running", "succeeded", "failed"]
    model: str
    prompt_version: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    draft_id: UUID | None
    error_code: Literal["evidence_changed", "invalid_snapshot", "generation_failed", "worker_interrupted"] | None
    dispatch_exhausted: bool
