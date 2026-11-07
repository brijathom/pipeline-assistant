"""Import public GitHub runs into the local database: python -m app.import_runs."""
import argparse
import sys

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_engine
from app.github import GitHubError, GitHubWorkflowRun, fetch_workflow_runs
from app.models import PipelineRun


def upsert_runs(session: Session, repository: str, runs: list[GitHubWorkflowRun]) -> int:
    """Persist a fetched batch; the caller owns the transaction and commit."""
    if not runs:
        return 0
    rows = [
        {
            "repository": repository,
            "github_run_id": run.id,
            "run_attempt": run.run_attempt,
            "workflow_name": run.name or "Unnamed workflow",
            "status": run.status,
            "conclusion": run.conclusion,
            "created_at": run.created_at,
            "started_at": run.run_started_at,
            # GitHub's run-list updated_at is not a completion timestamp.
            "completed_at": None,
        }
        for run in runs
    ]
    statement = insert(PipelineRun).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_pipeline_runs_attempt",
        set_={
            "workflow_name": statement.excluded.workflow_name,
            "status": statement.excluded.status,
            "conclusion": statement.excluded.conclusion,
            "started_at": statement.excluded.started_at,
        },
    )
    session.execute(statement)
    return len(rows)


def _limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 100") from None
    if not 1 <= limit <= 100:
        raise argparse.ArgumentTypeError("limit must be an integer from 1 to 100")
    return limit


def main() -> int:
    parser = argparse.ArgumentParser(description="Import a bounded batch of public GitHub Actions runs.")
    parser.add_argument("--limit", type=_limit, default=20, help="Latest runs to fetch, 1–100 (default: 20)")
    args = parser.parse_args()

    try:
        settings = get_settings()
        if not settings.github_repository:
            print("Set GITHUB_REPOSITORY=owner/name in backend/.env before importing.", file=sys.stderr)
            return 1
        runs = fetch_workflow_runs(settings.github_repository, args.limit)
        # Network I/O and validation finish before opening a database transaction.
        with Session(get_engine()) as session:
            count = upsert_runs(session, settings.github_repository, runs)
            session.commit()
    except ValidationError:
        print("Invalid backend configuration. Check DATABASE_URL and GITHUB_REPOSITORY.", file=sys.stderr)
        return 1
    except GitHubError as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Could not save runs. Check database connectivity and apply migrations.", file=sys.stderr)
        return 1

    print(f"Imported or refreshed {count} runs from {settings.github_repository}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
