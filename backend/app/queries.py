from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import PipelineRun
from app.schemas import PipelineMetricsResponse


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
