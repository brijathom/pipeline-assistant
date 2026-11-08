"""Generate a local rule-based report: python -m app.investigate_run --run-id ID."""
import argparse
import sys

from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.investigations import build_report, collect_evidence, evidence_fingerprint, save_investigation
from app.models import PipelineRun


class InvestigationError(Exception):
    """A safe local report-generation error."""


def parse_run_id(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("run-id must be a positive database ID.") from None
    if not 1 <= result <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("run-id must be a positive PostgreSQL bigint database ID.")
    return result


def investigate_run(run_id: int) -> None:
    engine = get_engine()
    with Session(engine) as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise InvestigationError("Stored run not found. Use its local database ID from /api/runs.")
        bundle = collect_evidence(session, run)
    # Generation happens outside the read transaction. A future model call can
    # follow this same boundary without holding a database connection open.
    report = build_report(bundle)
    with Session(engine) as session:
        run = session.get(PipelineRun, run_id, with_for_update=True)
        if run is None or evidence_fingerprint(collect_evidence(session, run)) != evidence_fingerprint(bundle):
            raise InvestigationError("Stored evidence changed during generation. Retry the investigation.")
        save_investigation(session, bundle, report)
        session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a rule-based investigation from stored curated evidence. No AI model is called.")
    parser.add_argument("--run-id", type=parse_run_id, required=True, help="Local run database ID")
    args = parser.parse_args()
    try:
        investigate_run(args.run_id)
    except ValidationError:
        print("Invalid configuration or investigation data. No report was saved.", file=sys.stderr)
        return 1
    except InvestigationError as error:
        print(str(error), file=sys.stderr)
        return 1
    except (SQLAlchemyError, ValueError):
        print("Could not save the investigation. Check database connectivity, migrations, and evidence validity.", file=sys.stderr)
        return 1
    print(f"Stored a rule-based investigation for run {args.run_id}. No AI model was called.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
