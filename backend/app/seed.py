"""Explicitly insert fictional local-demo runs; never runs at API startup."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import get_engine
from app.investigations import build_report, collect_evidence, save_investigation
from app.log_evidence import save_log_excerpt
from app.log_processing import process_log
from app.models import PipelineJob, PipelineJobLog, PipelineJobStep, PipelineRun


def seed_runs(session: Session) -> int:
    created = datetime(2026, 11, 7, 10, tzinfo=timezone.utc)
    rows = []
    for offset, conclusion in enumerate(("success", "failure", None)):
        timestamp = created + timedelta(hours=offset)
        rows.append({
            "repository": "demo/pipeline-assistant",
            "github_run_id": 1001 + offset,
            "run_attempt": 1,
            "workflow_name": "Demo: Build and Test",
            "status": "completed" if conclusion else "in_progress",
            "conclusion": conclusion,
            "created_at": timestamp,
            "started_at": timestamp + timedelta(seconds=10),
            "completed_at": timestamp + timedelta(minutes=3) if conclusion else None,
        })
    statement = (
        insert(PipelineRun)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_pipeline_runs_attempt")
        .returning(PipelineRun.id)
    )
    return len(session.scalars(statement).all())


def seed_demo_evidence(session: Session) -> None:
    """Add one fictional failed job and curated excerpt without replacing evidence."""
    run = session.scalar(select(PipelineRun).where(
        PipelineRun.repository == "demo/pipeline-assistant",
        PipelineRun.github_run_id == 1002, PipelineRun.run_attempt == 1,
    ))
    if run is None:
        raise ValueError("Seed the fictional runs before their evidence.")
    session.execute(insert(PipelineJob).values(
        pipeline_run_id=run.id, github_job_id=2001, name="Demo: Test backend",
        status="completed", conclusion="failure", started_at=run.started_at, completed_at=run.completed_at,
    ).on_conflict_do_nothing(constraint="uq_pipeline_jobs_run_job"))
    job_id = session.scalar(select(PipelineJob.id).where(
        PipelineJob.pipeline_run_id == run.id, PipelineJob.github_job_id == 2001,
    ))
    for number, name, conclusion in ((1, "Install dependencies", "success"), (7, "Run tests", "failure")):
        session.execute(insert(PipelineJobStep).values(
            pipeline_job_id=job_id, step_number=number, name=name, status="completed", conclusion=conclusion,
            started_at=run.started_at, completed_at=run.completed_at,
        ).on_conflict_do_nothing(constraint="uq_pipeline_job_steps_job_number"))
    if session.get(PipelineJobLog, job_id) is None:
        raw = (
            b"Starting build (fictional demo)\n"
            b"Installed dependencies\n"
            b"CI_TOKEN=demo-only-placeholder\n"
            b"Running tests\n"
            b"FAILED tests/test_health.py::test_health_status\n"
            b"AssertionError: expected HTTP 200, received HTTP 503\n"
            b"1 failed, 8 passed\n"
            b"Process completed with exit code 1\n"
        )
        save_log_excerpt(session, job_id, process_log(raw, start_line=3), source_kind="demo", is_curated=True)
    bundle = collect_evidence(session, run)
    save_investigation(session, bundle, build_report(bundle))


def main() -> None:
    with Session(get_engine()) as session:
        inserted = seed_runs(session)
        seed_demo_evidence(session)
        session.commit()
    print(f"Inserted {inserted} fictional demo runs.")
    print("Fictional failed-job evidence, a curated excerpt, and a rule-based investigation are available.")


if __name__ == "__main__":
    main()
