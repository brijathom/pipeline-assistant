"""Protected draft inbox. No generation, review, publication or other writes."""
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_session
from app.draft_reviews import DraftReviewError, validate_stored_draft
from app.investigations import collect_evidence, evidence_fingerprint
from app.models import PipelineInvestigationDraft, PipelineRun
from app.reviewer_auth import PRIVATE_HEADERS, require_reviewer
from app.schemas import ReviewerDraftDetails, ReviewerDraftSummary

router = APIRouter(prefix="/api/reviewer", tags=["reviewer"], dependencies=[Depends(require_reviewer)])


def _summary(draft: PipelineInvestigationDraft, run: PipelineRun) -> dict:
    return dict(id=draft.id, pipeline_run_id=run.id, repository=run.repository,
                github_run_id=run.github_run_id, run_attempt=run.run_attempt,
                status=draft.status, created_at=draft.created_at)


@router.get("/drafts", response_model=list[ReviewerDraftSummary])
def list_private_drafts(
    session: Annotated[Session, Depends(get_session)], response: Response,
    status: Literal["pending_review", "approved", "rejected"] = "pending_review",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[dict]:
    response.headers.update(PRIVATE_HEADERS)
    # Do not load the potentially large private document for inbox summaries.
    rows = session.execute(select(
        PipelineInvestigationDraft.id, PipelineInvestigationDraft.pipeline_run_id,
        PipelineRun.repository, PipelineRun.github_run_id, PipelineRun.run_attempt,
        PipelineInvestigationDraft.status, PipelineInvestigationDraft.created_at,
    ).join(PipelineRun, PipelineRun.id == PipelineInvestigationDraft.pipeline_run_id).where(
        PipelineInvestigationDraft.status == status,
    ).order_by(PipelineInvestigationDraft.created_at.desc(), PipelineInvestigationDraft.id.desc()).limit(limit))
    return [dict(row._mapping) for row in rows]


@router.get("/drafts/{draft_id}", response_model=ReviewerDraftDetails)
def get_private_draft(
    draft_id: UUID, session: Annotated[Session, Depends(get_session)], response: Response,
) -> ReviewerDraftDetails:
    response.headers.update(PRIVATE_HEADERS)
    draft = session.get(PipelineInvestigationDraft, draft_id, populate_existing=True)
    if draft is None:
        raise HTTPException(404, "Private draft not found.", headers=PRIVATE_HEADERS)
    run = session.get(PipelineRun, draft.pipeline_run_id, populate_existing=True)
    if run is None:
        raise HTTPException(404, "Private draft not found.", headers=PRIVATE_HEADERS)
    summary = _summary(draft, run)
    try:
        document = validate_stored_draft(draft)
    except (DraftReviewError, ValidationError, ValueError):
        return ReviewerDraftDetails(**summary, freshness="invalid")
    if evidence_fingerprint(collect_evidence(session, run)) != draft.evidence_fingerprint:
        return ReviewerDraftDetails(**summary, freshness="stale")
    return ReviewerDraftDetails(
        **summary, freshness="current", model=document.model,
        generated_at=document.generated_at, report=document.report,
    )
