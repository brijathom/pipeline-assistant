"""Generate a private local AI draft; never publishes or replaces a stored report."""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.ai_investigation import AIGenerationError, PROMPT_VERSION, THINKING_LEVEL, generate_ai_report
from app.config import get_settings
from app.database import get_engine
from app.investigate_run import InvestigationError, parse_run_id
from app.investigations import collect_evidence, evidence_fingerprint, validate_citations
from app.models import PipelineRun

DRAFT_DIRECTORY = Path(__file__).resolve().parents[1] / ".drafts"


def draft_investigation(run_id: int) -> Path:
    settings = get_settings()
    if settings.gemini_api_key is None or not settings.gemini_api_key.get_secret_value().strip():
        raise AIGenerationError("Set GEMINI_API_KEY in backend/.env before generating an AI draft.")
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise InvestigationError("Stored run not found. Use its local database ID from /api/runs.")
        bundle = collect_evidence(session, run)
    report = asyncio.run(generate_ai_report(bundle, api_key=settings.gemini_api_key, model_name=settings.ai_model))
    validate_citations(report, bundle)
    with Session(engine) as session:
        run = session.get(PipelineRun, run_id)
        if run is None or evidence_fingerprint(collect_evidence(session, run)) != evidence_fingerprint(bundle):
            raise InvestigationError("Stored evidence changed during generation. No draft was saved; retry the command.")
    document = {
        "status": "pending_review", "generation_method": "google-adk-v1", "model": settings.ai_model,
        "prompt_version": PROMPT_VERSION, "generated_at": datetime.now(timezone.utc).isoformat(),
        "thinking_level": THINKING_LEVEL,
        "evidence_fingerprint": evidence_fingerprint(bundle), "evidence_snapshot": bundle.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
    }
    serialized = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    DRAFT_DIRECTORY.mkdir(parents=True, exist_ok=True)
    path = DRAFT_DIRECTORY / f"run-{run_id}-{uuid4().hex}.json"
    created = False
    try:
        with path.open("x", encoding="utf-8", newline="\n") as output:
            created = True
            output.write(serialized)
    except OSError:
        if created:
            path.unlink(missing_ok=True)
        raise
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Send curated evidence to Gemini via Google ADK and save a private JSON draft for review.")
    parser.add_argument("--run-id", type=parse_run_id, required=True, help="Local run database ID, not the GitHub run ID")
    args = parser.parse_args()
    try:
        path = draft_investigation(args.run_id)
    except ValidationError:
        print("Invalid backend configuration or report data. No draft was saved.", file=sys.stderr)
        return 1
    except (AIGenerationError, InvestigationError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except (SQLAlchemyError, OSError, ValueError):
        print("Could not create the draft. Check database connectivity, migrations, and local file permissions.", file=sys.stderr)
        return 1
    print(f"Saved an unreviewed AI draft to {path}. The public report was not changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
