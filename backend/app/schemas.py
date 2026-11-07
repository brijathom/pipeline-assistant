from datetime import datetime

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
