from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


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
