"""Durable one-attempt investigation execution; caller/worker owns transactions."""
import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from pydantic import SecretStr, ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Session

from app.ai_investigation import GENERATION_TIMEOUT_SECONDS, MAX_PROMPT_BYTES, PROMPT_VERSION, THINKING_LEVEL, generate_ai_report
from app.draft_reviews import DraftDocument, DraftReviewError, store_draft
from app.investigations import EvidenceBundle, collect_evidence, evidence_fingerprint, validate_citations
from app.models import InvestigationTask, PipelineRun
from app.schemas import InvestigationReport

INTERRUPTED_AFTER_SECONDS = 120


class TaskError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def enqueue_task(session: Session, request_id: UUID, run_id: int, model: str) -> InvestigationTask:
    existing = session.get(InvestigationTask, request_id, populate_existing=True)
    if existing is not None:
        if existing.pipeline_run_id != run_id:
            raise TaskError("This request ID belongs to a different run.")
        return existing
    run = session.get(PipelineRun, run_id, with_for_update=True, populate_existing=True)
    if run is None:
        raise TaskError("Stored run not found.", 404)
    existing = session.get(InvestigationTask, request_id, populate_existing=True)
    if existing is not None:
        if existing.pipeline_run_id != run_id:
            raise TaskError("This request ID belongs to a different run.")
        return existing
    if session.scalar(select(InvestigationTask.id).where(
        InvestigationTask.pipeline_run_id == run_id, InvestigationTask.status.in_(("queued", "running")),
    )) is not None:
        raise TaskError("This run already has a queued or running investigation. Refresh the task list.")
    bundle = collect_evidence(session, run)
    if not any(job.lines for job in bundle.jobs):
        raise TaskError("No curated failed-job evidence is available. No task or model call was created.")
    snapshot = bundle.model_dump(mode="json")
    if len(json.dumps(snapshot, ensure_ascii=False).encode("utf-8")) > MAX_PROMPT_BYTES:
        raise TaskError("Curated evidence exceeds the 64 KiB prompt limit. No task was created.")
    task = InvestigationTask(id=request_id, pipeline_run_id=run_id, model=model, prompt_version=PROMPT_VERSION,
        evidence_snapshot=snapshot, evidence_fingerprint=evidence_fingerprint(bundle))
    session.add(task)
    session.flush()
    return task


def _failed(task: InvestigationTask, code: str) -> None:
    task.status, task.error_code, task.completed_at = "failed", code, func.clock_timestamp()


def _current(session: Session, task: InvestigationTask) -> bool:
    run = session.get(PipelineRun, task.pipeline_run_id, populate_existing=True)
    return run is not None and evidence_fingerprint(collect_evidence(session, run)) == task.evidence_fingerprint


def _claim(session: Session, task_id: UUID) -> tuple[str, EvidenceBundle | None, str | None]:
    task = session.get(InvestigationTask, task_id, with_for_update=True, populate_existing=True)
    if task is None:
        raise TaskError("Investigation task not found.", 404)
    if task.status != "queued":
        return task.status, None, None
    task.status, task.started_at = "running", func.clock_timestamp()
    try:
        bundle = EvidenceBundle.model_validate(task.evidence_snapshot)
        valid = (bundle.run_id == task.pipeline_run_id and evidence_fingerprint(bundle) == task.evidence_fingerprint
                 and task.prompt_version == PROMPT_VERSION and re.fullmatch(r"gemini-[a-z0-9.-]{1,80}", task.model)
                 and any(job.lines for job in bundle.jobs)
                 and len(json.dumps(task.evidence_snapshot, ensure_ascii=False).encode()) <= MAX_PROMPT_BYTES)
    except (ValidationError, ValueError):
        valid = False
    if not valid:
        _failed(task, "invalid_snapshot")
        return "failed", None, None
    if not _current(session, task):
        _failed(task, "evidence_changed")
        return "failed", None, None
    return "running", bundle, task.model


async def execute_task(
    task_id: UUID, bind: Engine | Connection, *, api_key: SecretStr | None = None,
    generate: Callable[..., Awaitable[InvestigationReport]] | None = None,
) -> str:
    if generate is None and (api_key is None or not api_key.get_secret_value().strip()):
        raise TaskError("Configure GEMINI_API_KEY before starting the worker.", 503)
    # The claim commits before any external call. A redelivery never reclaims
    # running/terminal tasks, including after an ambiguous worker interruption.
    with Session(bind, join_transaction_mode="create_savepoint") as session:
        status, bundle, model = _claim(session, task_id)
        session.commit()
    if bundle is None:
        return status
    error = None
    report = None
    try:
        async with asyncio.timeout(GENERATION_TIMEOUT_SECONDS):
            report = await (generate or generate_ai_report)(bundle, api_key=api_key, model_name=model)
        report = InvestigationReport.model_validate(report.model_dump())
        validate_citations(report, bundle)
    except Exception:
        error = "generation_failed"  # Never retain provider errors or output.
    with Session(bind, join_transaction_mode="create_savepoint") as session:
        task = session.get(InvestigationTask, task_id, with_for_update=True, populate_existing=True)
        if task is None:
            raise TaskError("Investigation task not found.", 404)
        if task.status != "running":
            return task.status  # Explicit interruption fences off a late result.
        if error:
            _failed(task, error)
        elif not _current(session, task):
            _failed(task, "evidence_changed")
        else:
            try:
                document = DraftDocument(
                    generation_method="google-adk-v1", model=model, prompt_version=PROMPT_VERSION,
                    generated_at=datetime.now(timezone.utc), thinking_level=THINKING_LEVEL,
                    evidence_fingerprint=task.evidence_fingerprint, evidence_snapshot=bundle, report=report,
                )
                task.draft_id = store_draft(session, document)
                task.status, task.completed_at = "succeeded", func.clock_timestamp()
            except (DraftReviewError, ValidationError, ValueError):
                _failed(task, "evidence_changed")
        status = task.status
        session.commit()  # Draft and successful task state commit together.
        return status


def expire_interrupted_task(session: Session, task_id: UUID) -> None:
    task = session.get(InvestigationTask, task_id, with_for_update=True, populate_existing=True)
    if task is None:
        raise TaskError("Investigation task not found.", 404)
    cutoff = session.scalar(select(func.clock_timestamp())) - timedelta(seconds=INTERRUPTED_AFTER_SECONDS)
    if task.status != "running" or task.started_at >= cutoff:
        raise TaskError("Only running tasks older than 120 seconds can be marked interrupted.")
    _failed(task, "worker_interrupted")
