from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.queries import run_metrics
from app.repositories import RepositoryName
from app.schemas import PipelineMetricsResponse, PipelinePerformanceResponse
from app.performance import PerformanceWindow, job_performance, utc_now

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


@router.get("", response_model=PipelineMetricsResponse)
def get_metrics(
    session: Annotated[Session, Depends(get_session)],
    repository: Annotated[RepositoryName | None, Query()] = None,
) -> PipelineMetricsResponse:
    return run_metrics(session, repository)


@router.get("/performance", response_model=PipelinePerformanceResponse)
def get_performance(
    session: Annotated[Session, Depends(get_session)],
    now: Annotated[datetime, Depends(utc_now)],
    days: Annotated[PerformanceWindow, Query()] = PerformanceWindow.month,
    repository: Annotated[RepositoryName | None, Query()] = None,
) -> PipelinePerformanceResponse:
    try:
        session.execute(text("SET LOCAL statement_timeout = '5000ms'"))
        return job_performance(session, days, repository, now)
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Performance metrics are temporarily unavailable.") from None
