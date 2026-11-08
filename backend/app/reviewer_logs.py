"""Protected review of stored processed GitHub excerpts; no external calls."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_session
from app.investigations import document_fingerprint
from app.models import PipelineJob, PipelineJobLog, PipelineRun
from app.queries import serialize_log_excerpt
from app.reviewer_auth import require_reviewer
from app.schemas import LogCurationRequest, ReviewerLogDetails, ReviewerLogSummary

router = APIRouter(prefix="/api/reviewer/logs", tags=["reviewer"], dependencies=[Depends(require_reviewer)])
JobId = Annotated[int, Path(gt=0, le=9_223_372_036_854_775_807)]


def _summary(log: PipelineJobLog, job: PipelineJob, run: PipelineRun) -> dict:
    return dict(job_id=job.id, pipeline_run_id=run.id, repository=run.repository,
                github_run_id=run.github_run_id, run_attempt=run.run_attempt,
                job_name=job.name, is_curated=log.is_curated, updated_at=log.updated_at)


def _load(session: Session, job_id: int, *, lock: bool = False):
    statement = select(PipelineJobLog, PipelineJob, PipelineRun).join(
        PipelineJob, PipelineJob.id == PipelineJobLog.pipeline_job_id,
    ).join(PipelineRun, PipelineRun.id == PipelineJob.pipeline_run_id).where(
        PipelineJobLog.pipeline_job_id == job_id, PipelineJobLog.source_kind == "github",
    ).execution_options(populate_existing=True)
    if lock:
        statement = statement.with_for_update(of=PipelineJobLog)
    row = session.execute(statement).one_or_none()
    if row is None:
        raise HTTPException(404, "Imported log excerpt not found.")
    return row


def _details(log: PipelineJobLog, job: PipelineJob, run: PipelineRun) -> ReviewerLogDetails:
    # Include content, source range, flags and timestamp, so replacing an excerpt
    # or withdrawing/recurating it invalidates an operator's earlier review.
    payload = {**_summary(log, job, run), "excerpt": serialize_log_excerpt(log)}
    detail = ReviewerLogDetails(**payload, revision="")
    detail.revision = document_fingerprint(detail.model_dump(mode="json", exclude={"revision"}))
    return detail


@router.get("", response_model=list[ReviewerLogSummary])
def list_private_logs(
    session: Annotated[Session, Depends(get_session)],
    is_curated: bool = False, limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict]:
    rows = session.execute(select(
        PipelineJob.id.label("job_id"), PipelineRun.id.label("pipeline_run_id"),
        PipelineRun.repository, PipelineRun.github_run_id, PipelineRun.run_attempt,
        PipelineJob.name.label("job_name"), PipelineJobLog.is_curated, PipelineJobLog.updated_at,
    ).select_from(PipelineJob).join(PipelineJobLog, PipelineJobLog.pipeline_job_id == PipelineJob.id).join(
        PipelineRun, PipelineRun.id == PipelineJob.pipeline_run_id,
    ).where(PipelineJobLog.source_kind == "github", PipelineJobLog.is_curated == is_curated).order_by(
        PipelineJobLog.updated_at.desc(), PipelineJob.id.desc(),
    ).limit(limit))
    return [dict(row._mapping) for row in rows]


@router.get("/{job_id}", response_model=ReviewerLogDetails)
def get_private_log(job_id: JobId, session: Annotated[Session, Depends(get_session)]) -> ReviewerLogDetails:
    return _details(*_load(session, job_id))


@router.post("/{job_id}/curation", response_model=ReviewerLogDetails)
def curate_private_log(
    job_id: JobId, decision: LogCurationRequest, session: Annotated[Session, Depends(get_session)],
) -> ReviewerLogDetails:
    try:
        log, job, run = _load(session, job_id, lock=True)
        if _details(log, job, run).revision != decision.expected_revision:
            raise HTTPException(409, "The excerpt changed. Reload and review it again before changing curation.")
        if log.is_curated != decision.is_curated:
            log.is_curated = decision.is_curated
            # A new timestamp keeps old evidence fingerprints stale even after
            # withdrawing and recurating identical text. No content is edited.
            log.updated_at = func.clock_timestamp()
            session.flush()
        result = _details(log, job, run)
        session.commit()
        return result
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Could not confirm curation. Reload to verify its status before trying again.") from None
