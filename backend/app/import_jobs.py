"""Import one stored attempt's public GitHub jobs: python -m app.import_jobs --run-id ID."""
import argparse
import sys

from pydantic import ValidationError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import get_engine
from app.github import GitHubError, GitHubJob, fetch_workflow_jobs
from app.models import PipelineJob, PipelineJobStep, PipelineRun
from app.repositories import normalize_repository


class JobImportError(Exception):
    """A safe command error, without database or transport internals."""


def upsert_jobs(session: Session, run_id: int, jobs: list[GitHubJob]) -> tuple[int, int]:
    """Persist validated jobs and steps; the caller owns the transaction and commit."""
    if not jobs:
        return 0, 0
    rows = [{
        "pipeline_run_id": run_id, "github_job_id": job.id, "name": job.name,
        "status": job.status, "conclusion": job.conclusion,
        "started_at": job.started_at, "completed_at": job.completed_at,
    } for job in jobs]
    statement = insert(PipelineJob).values(rows)
    statement = statement.on_conflict_do_update(
        constraint="uq_pipeline_jobs_run_job",
        set_={field: getattr(statement.excluded, field) for field in (
            "name", "status", "conclusion", "started_at", "completed_at",
        )},
    ).returning(PipelineJob.id, PipelineJob.github_job_id)
    job_ids = {row.github_job_id: row.id for row in session.execute(statement)}

    step_count = 0
    # A job at a time keeps statements bounded even when an attempt has many steps.
    for job in jobs:
        if not job.steps:
            continue
        step_rows = [{
            "pipeline_job_id": job_ids[job.id], "step_number": step.number,
            "name": step.name, "status": step.status, "conclusion": step.conclusion,
            "started_at": step.started_at, "completed_at": step.completed_at,
        } for step in job.steps]
        steps = insert(PipelineJobStep).values(step_rows)
        session.execute(steps.on_conflict_do_update(
            constraint="uq_pipeline_job_steps_job_number",
            set_={field: getattr(steps.excluded, field) for field in (
                "name", "status", "conclusion", "started_at", "completed_at",
            )},
        ))
        step_count += len(step_rows)
    return len(rows), step_count


def _run_id(value: str) -> int:
    try:
        run_id = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("run-id must be a positive database ID") from None
    if not 1 <= run_id <= 9_223_372_036_854_775_807:
        raise argparse.ArgumentTypeError("run-id must be a positive PostgreSQL bigint database ID")
    return run_id


def import_jobs(run_id: int) -> tuple[int, int]:
    engine = get_engine()
    # Read the source identity, then release the connection before network I/O.
    with Session(engine) as session:
        run = session.get(PipelineRun, run_id)
        if run is None:
            raise JobImportError("Stored run not found. Use its local database ID from /api/runs.")
        source = (run.repository, run.github_run_id, run.run_attempt)
    try:
        repository = normalize_repository(source[0])
        if repository.startswith("demo/"):
            raise JobImportError("Fictional demo runs have no GitHub jobs. Select an imported public run.")
        jobs = fetch_workflow_jobs(repository, source[1], source[2])
    except ValueError:
        raise JobImportError("The stored repository, GitHub run ID or attempt is invalid.") from None

    with Session(engine) as session:
        # Lock only during writes, and recheck identity in case the row changed during the fetch.
        run = session.get(PipelineRun, run_id, with_for_update=True)
        if run is None or (run.repository, run.github_run_id, run.run_attempt) != source:
            raise JobImportError("The stored run changed during the fetch. Retry the import.")
        counts = upsert_jobs(session, run_id, jobs)
        session.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description="Import jobs and steps for one stored public GitHub run attempt.")
    parser.add_argument("--run-id", type=_run_id, required=True, help="Local database ID from GET /api/runs")
    args = parser.parse_args()
    try:
        jobs, steps = import_jobs(args.run_id)
    except ValidationError:
        print("Invalid backend configuration. Check backend/.env and environment variables.", file=sys.stderr)
        return 1
    except (JobImportError, GitHubError) as error:
        print(str(error), file=sys.stderr)
        return 1
    except SQLAlchemyError:
        print("Could not import jobs. Check database connectivity and apply migrations.", file=sys.stderr)
        return 1
    job_label = "job" if jobs == 1 else "jobs"
    step_label = "step" if steps == 1 else "steps"
    print(f"Imported or refreshed {jobs} {job_label} and {steps} {step_label} for stored run {args.run_id}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
