from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.queries import run_metrics
from app.repositories import RepositoryName
from app.schemas import PipelineMetricsResponse

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=PipelineMetricsResponse)
def get_metrics(
    session: Annotated[Session, Depends(get_session)],
    repository: Annotated[RepositoryName | None, Query()] = None,
) -> PipelineMetricsResponse:
    return run_metrics(session, repository)
