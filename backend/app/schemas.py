from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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
    generation_method: Literal["rules-v1"] | None = None
    generated_at: datetime | None = None
    evidence_fingerprint: str | None = None
    report: InvestigationReport | None = None
