from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PipelineJob, PipelineJobLog, PipelineJobStep, PipelineRun
from app.schemas import (
    LogLineResponse, PipelineJobLogResponse, PipelineJobResponse, PipelineJobStepResponse, PipelineMetricsResponse,
)


def find_run(session: Session, run_id: int) -> PipelineRun | None:
    """Find one stored attempt by its local primary key."""
    return session.get(PipelineRun, run_id)


def find_curated_job_log(session: Session, job_id: int) -> PipelineJobLogResponse | None:
    record = session.scalar(select(PipelineJobLog).where(
        PipelineJobLog.pipeline_job_id == job_id, PipelineJobLog.is_curated.is_(True),
    ))
    if record is None:
        return None
    texts = record.text.split("\n") if record.line_count else []
    return PipelineJobLogResponse(
        lines=[LogLineResponse(source_line=record.start_line + index, text=text) for index, text in enumerate(texts)],
        total_source_lines=record.total_source_lines, truncated=record.truncated,
        redactions_applied=record.redactions_applied, source_kind=record.source_kind, updated_at=record.updated_at,
    )


def list_run_jobs(session: Session, run_id: int) -> list[PipelineJobResponse]:
    """Read one attempt's evidence in two queries, without per-job step lookups."""
    jobs = list(session.scalars(
        select(PipelineJob).where(PipelineJob.pipeline_run_id == run_id)
        .order_by(PipelineJob.github_job_id, PipelineJob.id),
    ))
    if not jobs:
        return []
    steps_by_job: dict[int, list[PipelineJobStepResponse]] = {job.id: [] for job in jobs}
    steps = session.scalars(
        select(PipelineJobStep).where(PipelineJobStep.pipeline_job_id.in_(steps_by_job))
        .order_by(PipelineJobStep.pipeline_job_id, PipelineJobStep.step_number),
    )
    for step in steps:
        steps_by_job[step.pipeline_job_id].append(PipelineJobStepResponse.model_validate(step))
    return [PipelineJobResponse(
        id=job.id, github_job_id=job.github_job_id, name=job.name,
        status=job.status, conclusion=job.conclusion,
        started_at=job.started_at, completed_at=job.completed_at,
        steps=steps_by_job[job.id],
    ) for job in jobs]


def list_runs(session: Session, limit: int, repository: str | None = None) -> list[PipelineRun]:
    """Return at most limit runs, newest created_at first, then highest id."""
    statement = (
        select(PipelineRun)
        .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .limit(limit)
    )
    if repository is not None:
        statement = statement.where(PipelineRun.repository == repository)
    return list(session.scalars(statement))


def run_metrics(session: Session, repository: str | None = None) -> PipelineMetricsResponse:
    """Aggregate all stored attempts in one database query, without pagination."""
    completed = PipelineRun.status == "completed"
    statement = select(
        func.count().label("total_runs"),
        func.count().filter(completed & (PipelineRun.conclusion == "success")).label("successful_runs"),
        func.count().filter(
            completed & PipelineRun.conclusion.in_(("failure", "timed_out")),
        ).label("failed_runs"),
        func.count().filter(PipelineRun.status == "in_progress").label("in_progress_runs"),
    ).select_from(PipelineRun)
    if repository is not None:
        statement = statement.where(PipelineRun.repository == repository)
    counts = session.execute(statement).mappings().one()
    return PipelineMetricsResponse(repository=repository, **counts)
