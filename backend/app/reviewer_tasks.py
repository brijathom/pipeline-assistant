"""Protected task submission/status. Generation occurs only in a separate worker."""
from collections.abc import Callable
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_session
from app.investigation_tasks import TaskError, enqueue_task
from app.models import InvestigationTask
from app.reviewer_auth import require_reviewer
from app.schemas import InvestigationTaskRequest, InvestigationTaskResponse
from app.task_queries import list_task_summaries, task_summary_query
from app.task_events import get_task_snapshot_reader, open_task_stream

router = APIRouter(prefix="/api/reviewer/tasks", tags=["reviewer"], dependencies=[Depends(require_reviewer)])


@router.get("", response_model=list[InvestigationTaskResponse])
def list_tasks(session: Annotated[Session, Depends(get_session)], limit: Annotated[int, Query(ge=1, le=50)] = 20) -> list[dict]:
    return list_task_summaries(session, limit)


@router.get("/events")
async def stream_tasks(request: Request, read: Annotated[Callable, Depends(get_task_snapshot_reader)]):
    return await open_task_stream(request, read)


@router.get("/{task_id}", response_model=InvestigationTaskResponse)
def get_task(task_id: UUID, session: Annotated[Session, Depends(get_session)]) -> dict:
    row = session.execute(task_summary_query().where(InvestigationTask.id == task_id)).one_or_none()
    if row is None:
        raise HTTPException(404, "Investigation task not found.")
    return dict(row._mapping)


@router.post("", response_model=InvestigationTaskResponse, status_code=202)
def create_task(
    request: InvestigationTaskRequest, settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[Session, Depends(get_session)],
) -> dict:
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip():
        raise HTTPException(503, "AI generation is not configured. No task was created.")
    try:
        enqueue_task(session, request.request_id, request.run_id, settings.ai_model)
        result = get_task(request.request_id, session)
        session.commit()
        return result
    except TaskError as error:
        raise HTTPException(error.status_code, str(error)) from None
    except IntegrityError:
        session.rollback()
        raise HTTPException(409, "The request ID or active task already exists. Refresh task status.") from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Could not confirm task submission. Refresh task status before retrying the same request ID.") from None
