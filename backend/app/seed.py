"""Explicitly insert fictional local-demo runs; never runs at API startup."""
from datetime import datetime, timedelta, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import get_engine
from app.models import PipelineRun


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


def main() -> None:
    with Session(get_engine()) as session:
        inserted = seed_runs(session)
        session.commit()
    print(f"Inserted {inserted} fictional demo runs.")


if __name__ == "__main__":
    main()
