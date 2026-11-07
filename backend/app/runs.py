from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import PipelineRun
from app.queries import find_run, list_runs
from app.repositories import RepositoryName
from app.schemas import PipelineRunResponse

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
