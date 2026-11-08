"""Metadata-only task queries shared by status reads and progress streams."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import InvestigationTask, PipelineRun


def task_summary_query():
    return select(
        InvestigationTask.id, InvestigationTask.pipeline_run_id, PipelineRun.repository,
        PipelineRun.github_run_id, PipelineRun.run_attempt, InvestigationTask.status,
        InvestigationTask.model, InvestigationTask.prompt_version, InvestigationTask.created_at,
        InvestigationTask.started_at, InvestigationTask.completed_at, InvestigationTask.draft_id, InvestigationTask.error_code,
        ((InvestigationTask.dispatch_attempts >= 5) & InvestigationTask.dispatched_at.is_(None)
         & (InvestigationTask.status == "queued")).label("dispatch_exhausted"),
    ).join(PipelineRun, PipelineRun.id == InvestigationTask.pipeline_run_id)


def list_task_summaries(session: Session, limit: int = 20) -> list[dict]:
    rows = session.execute(task_summary_query().order_by(InvestigationTask.created_at.desc(), InvestigationTask.id.desc()).limit(limit))
    return [dict(row._mapping) for row in rows]
