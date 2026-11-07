from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PipelineRun


def list_runs(session: Session, limit: int) -> list[PipelineRun]:
    """Return at most limit runs, newest created_at first, then highest id."""
    statement = (
        select(PipelineRun)
        .order_by(PipelineRun.created_at.desc(), PipelineRun.id.desc())
        .limit(limit)
    )
    return list(session.scalars(statement))
