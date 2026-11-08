"""Private drafts, local review and explicit publication; never calls AI."""
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.investigations import EvidenceBundle, collect_evidence, document_fingerprint, evidence_fingerprint, validate_citations
from app.models import PipelineInvestigation, PipelineInvestigationDraft, PipelineRun
from app.schemas import InvestigationReport

MAX_DRAFT_BYTES = 1_048_576


class DraftReviewError(Exception):
    """Safe error without private evidence or provider output."""

    def __init__(self, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


class DraftDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    status: Literal["pending_review"] = "pending_review"
    generation_method: Literal["google-adk-v1"]
    model: str = Field(pattern=r"^gemini-[a-z0-9.-]{1,80}$")
    prompt_version: str = Field(min_length=1, max_length=100)
    generated_at: AwareDatetime
    thinking_level: Literal["low"]
    evidence_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    evidence_snapshot: EvidenceBundle
    report: InvestigationReport


def load_draft(path: Path) -> DraftDocument:
    with path.open("rb") as source:
        raw = source.read(MAX_DRAFT_BYTES + 1)
    if len(raw) > MAX_DRAFT_BYTES:
        raise DraftReviewError("Draft exceeds the 1 MiB import limit.")
    document = DraftDocument.model_validate_json(raw)
    _validate_document(document)
    return document


def _validate_document(document: DraftDocument) -> None:
    if evidence_fingerprint(document.evidence_snapshot) != document.evidence_fingerprint:
        raise DraftReviewError("Draft evidence does not match its fingerprint.")
    validate_citations(document.report, document.evidence_snapshot)


def _require_current_evidence(session: Session, run_id: int, fingerprint: str) -> None:
    run = session.get(PipelineRun, run_id, populate_existing=True)
    if run is None:
        raise DraftReviewError("Stored run not found.", status_code=404)
    if evidence_fingerprint(collect_evidence(session, run)) != fingerprint:
        raise DraftReviewError("Evidence changed or lost curation. Generate and review a new draft.")


def store_draft(session: Session, document: DraftDocument) -> UUID:
    """Caller owns the transaction. Reimporting preserves an existing decision."""
    _validate_document(document)
    # Keep the generated payload unchanged; the authoritative review state lives
    # in separate columns, not in the original file's pending_review field.
    payload = document.model_dump(mode="json", exclude={"status"})
    fingerprint = document_fingerprint(payload)
    existing = session.scalar(select(PipelineInvestigationDraft.id).where(
        PipelineInvestigationDraft.content_fingerprint == fingerprint,
    ))
    if existing is not None:
        return existing
    run_id = document.evidence_snapshot.run_id
    _require_current_evidence(session, run_id, document.evidence_fingerprint)
    statement = insert(PipelineInvestigationDraft).values(
        id=uuid4(), pipeline_run_id=run_id, content_fingerprint=fingerprint,
        evidence_fingerprint=document.evidence_fingerprint, document=payload,
    ).on_conflict_do_nothing(index_elements=[PipelineInvestigationDraft.content_fingerprint])
    draft_id = session.scalar(statement.returning(PipelineInvestigationDraft.id))
    if draft_id is None:
        draft_id = session.scalar(select(PipelineInvestigationDraft.id).where(
            PipelineInvestigationDraft.content_fingerprint == fingerprint,
        ))
    if draft_id is None:
        raise DraftReviewError("The private draft changed during import. Retry the import.")
    return draft_id


def review_draft(session: Session, draft_id: UUID, decision: str, reviewer: str, note: str) -> None:
    reviewer, note = reviewer.strip(), note.strip()
    if decision not in {"approved", "rejected"} or not 1 <= len(reviewer) <= 100 or not 1 <= len(note) <= 1000:
        raise DraftReviewError("Use approved/rejected, a reviewer of 1-100 characters, and a note of 1-1000 characters.", status_code=422)
    # Serialize reviewers and refresh even when the Session cached an older state.
    draft = session.get(PipelineInvestigationDraft, draft_id, with_for_update=True, populate_existing=True)
    if draft is None:
        raise DraftReviewError("Private draft not found.", status_code=404)
    if draft.status != "pending_review":
        raise DraftReviewError("This draft has already been reviewed. Its decision cannot be overwritten.")
    if decision == "approved":
        validate_stored_draft(draft)
        _require_current_evidence(session, draft.pipeline_run_id, draft.evidence_fingerprint)
    # Rejection remains possible after evidence is withdrawn. Approval endorses
    # this exact payload and fingerprint, not any later version of the evidence.
    draft.status = decision
    draft.reviewed_by = reviewer
    draft.review_note = note
    draft.reviewed_at = func.now()
    session.flush()


def validate_stored_draft(draft: PipelineInvestigationDraft) -> DraftDocument:
    document = DraftDocument.model_validate(draft.document)
    _validate_document(document)
    if (document.evidence_snapshot.run_id != draft.pipeline_run_id
            or document.evidence_fingerprint != draft.evidence_fingerprint
            or document_fingerprint(draft.document) != draft.content_fingerprint):
        raise DraftReviewError("Stored draft integrity check failed.")
    return document


def publish_draft(session: Session, draft_id: UUID) -> None:
    """Publish only an approved, unchanged document with current curated evidence."""
    draft = session.get(PipelineInvestigationDraft, draft_id, with_for_update=True, populate_existing=True)
    if draft is None:
        raise DraftReviewError("Private draft not found.", status_code=404)
    if draft.status != "approved":
        raise DraftReviewError("Only an approved draft can be published.")
    document = validate_stored_draft(draft)
    # Serialize publishers for the same run. No network or model call occurs
    # while these locks are held. The public read path also rechecks freshness.
    if session.get(PipelineRun, draft.pipeline_run_id, with_for_update=True, populate_existing=True) is None:
        raise DraftReviewError("Stored run not found.", status_code=404)
    _require_current_evidence(session, draft.pipeline_run_id, draft.evidence_fingerprint)
    statement = insert(PipelineInvestigation).values(
        pipeline_run_id=draft.pipeline_run_id, generation_method=document.generation_method,
        evidence_fingerprint=document.evidence_fingerprint,
        evidence_snapshot=document.evidence_snapshot.model_dump(mode="json"),
        report=document.report.model_dump(mode="json"), generated_at=document.generated_at,
        source_draft_id=draft.id,
    )
    session.execute(statement.on_conflict_do_update(
        index_elements=[PipelineInvestigation.pipeline_run_id],
        set_={key: getattr(statement.excluded, key) for key in (
            "generation_method", "evidence_fingerprint", "evidence_snapshot", "report", "generated_at", "source_draft_id",
        )},
    ))
