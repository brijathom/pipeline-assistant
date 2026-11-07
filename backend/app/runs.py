from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_session
from app.models import PipelineRun
from app.queries import list_runs
from app.schemas import PipelineRunResponse

router = APIRouter(prefix="/api/runs", tags=["runs"])


@router.get("", response_model=list[PipelineRunResponse])
def get_runs(
    session: Annotated[Session, Depends(get_session)],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[PipelineRun]:
    return list_runs(session, limit)
