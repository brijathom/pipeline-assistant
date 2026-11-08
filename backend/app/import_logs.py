"""Explicitly import one job's processed excerpt: python -m app.import_logs --job-id ID."""
import argparse
import subprocess
import sys

from pydantic import SecretStr, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.github import GitHubError, fetch_job_log_excerpt
from app.log_evidence import save_log_excerpt
from app.models import PipelineJob, PipelineRun


class LogImportError(Exception):
    """A safe local command error."""


def _positive_id(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Expected a positive integer.") from None
    if not 1 <= result <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("Expected a positive PostgreSQL bigint.")
    return result


def _source(session: Session, job_id: int, *, lock: bool = False) -> tuple | None:
    statement = select(
        PipelineRun.repository, PipelineRun.github_run_id, PipelineRun.run_attempt,
        PipelineJob.pipeline_run_id, PipelineJob.github_job_id,
    ).join(PipelineJob, PipelineJob.pipeline_run_id == PipelineRun.id).where(PipelineJob.id == job_id)
    if lock:
        statement = statement.with_for_update()
    row = session.execute(statement).one_or_none()
    return tuple(row) if row is not None else None


def github_cli_token() -> SecretStr:
    """Read an existing operator credential in memory; never print or persist it."""
    try:
        result = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True, timeout=10, check=True)
    except (OSError, subprocess.SubprocessError):
        raise LogImportError("GitHub CLI authentication is unavailable. Sign in locally with gh before using this option.") from None
    if not result.stdout.strip():
        raise LogImportError("GitHub CLI returned no credential. Sign in locally with gh.")
    return SecretStr(result.stdout.strip())


def import_log(job_id: int, start_line: int = 1, *, api_token: SecretStr | None = None) -> None:
    engine = get_engine()
    with Session(engine) as session:
        source = _source(session, job_id)
    if source is None:
        raise LogImportError("Stored job not found. Use its local ID from /api/runs/{id}/jobs.")
    if source[0].startswith("demo/"):
        raise LogImportError("Fictional demo jobs have no GitHub logs. Run the demo seeder instead.")
    try:
        auth = {"api_token": api_token} if api_token is not None else {}
        excerpt = fetch_job_log_excerpt(source[0], source[4], start_line=start_line, **auth)
    except ValueError:
        raise LogImportError("The stored job identity or requested source line is invalid.") from None
    with Session(engine) as session:
        if _source(session, job_id, lock=True) != source:
            raise LogImportError("The stored job or run changed during the fetch. Retry the import.")
        save_log_excerpt(session, job_id, excerpt)
        session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description="Store one public job's processed excerpt without publishing it.")
    parser.add_argument("--job-id", type=_positive_id, required=True, help="Local job database ID")
    parser.add_argument("--start-line", type=_positive_id, default=1, help="First source line to include")
    parser.add_argument("--github-cli-auth", action="store_true", help="Use the existing local gh credential in memory for API reads; never save it or send it to log storage")
    args = parser.parse_args()
    try:
        if args.github_cli_auth:
            import_log(args.job_id, args.start_line, api_token=github_cli_token())
        else:
            import_log(args.job_id, args.start_line)
    except ValidationError:
        print("Invalid backend configuration. Check backend/.env and environment variables.", file=sys.stderr)
        return 1
    except (LogImportError, GitHubError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Could not store the excerpt. Check database connectivity and apply migrations.", file=sys.stderr)
        return 1
    print(f"Stored a processed excerpt for job {args.job_id}. It is hidden from the public API pending curation.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
