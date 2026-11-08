from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.investigations import find_investigation
from app.models import PipelineJob, PipelineRun
from app.queries import find_curated_job_log, find_run, list_run_jobs, list_runs
from app.repositories import RepositoryName
from app.schemas import PipelineInvestigationResponse, PipelineJobLogResponse, PipelineJobResponse, PipelineRunResponse

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[PipelineRunResponse])
def get_runs(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    repository: Annotated[RepositoryName | None, Query()] = None,
) -> list[PipelineRun]:
    return list_runs(session, limit, repository)


@router.get("/{run_id}", response_model=PipelineRunResponse)
def get_run(
    run_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
    session: Annotated[Session, Depends(get_session)],
) -> PipelineRun:
    run = find_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return run


@router.get("/{run_id}/jobs", response_model=list[PipelineJobResponse])
def get_run_jobs(
    run_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
    session: Annotated[Session, Depends(get_session)],
) -> list[PipelineJobResponse]:
    if find_run(session, run_id) is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return list_run_jobs(session, run_id)


@router.get("/{run_id}/jobs/{job_id}/log", response_model=PipelineJobLogResponse | None)
def get_job_log(
    run_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
    job_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
    session: Annotated[Session, Depends(get_session)],
    response: Response,
) -> PipelineJobLogResponse | None:
    response.headers["Cache-Control"] = "no-store"
    if session.scalar(select(PipelineJob.id).where(
        PipelineJob.id == job_id, PipelineJob.pipeline_run_id == run_id,
    )) is None:
        raise HTTPException(status_code=404, detail="Pipeline job not found for this run")
    return find_curated_job_log(session, job_id)


@router.get("/{run_id}/investigation", response_model=PipelineInvestigationResponse)
def get_investigation(
    run_id: Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)],
    session: Annotated[Session, Depends(get_session)],
    response: Response,
) -> PipelineInvestigationResponse:
    response.headers["Cache-Control"] = "no-store"
    run = find_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Pipeline run not found")
    return find_investigation(session, run)
