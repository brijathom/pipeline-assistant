from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.schema import CreateSchema

from app.models import PipelineJob, PipelineJobLog, PipelineJobStep, PipelineRun


def test_job_migration_round_trip_preserves_existing_runs(test_engine: Engine) -> None:
    # A transaction-local schema isolates destructive downgrade checks from all other data.
    schema = f"migration_test_{uuid4().hex}"
    with test_engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.execute(CreateSchema(schema))
            # The identifier is generated locally from a fixed prefix and UUID hex.
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "0001")
            run_id = connection.scalar(PipelineRun.__table__.insert().values(
                repository="example/migration", github_run_id=1234,
                workflow_name="Existing run", status="queued",
                created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
            ).returning(PipelineRun.id))

            command.upgrade(config, "head")
            tables = inspect(connection).get_table_names(schema=schema)
            assert {"pipeline_runs", "pipeline_jobs", "pipeline_job_steps"}.issubset(tables)
            job_id = connection.scalar(PipelineJob.__table__.insert().values(
                pipeline_run_id=run_id, github_job_id=5678, name="Build", status="queued",
            ).returning(PipelineJob.id))
            connection.execute(PipelineJobStep.__table__.insert().values(
                pipeline_job_id=job_id, step_number=1, name="Test", status="queued",
            ))

            connection.execute(PipelineJobLog.__table__.insert().values(
                pipeline_job_id=job_id, text="processed evidence", start_line=1, line_count=1,
                total_source_lines=1, truncated=False, redactions_applied=False, source_kind="github",
            ))
            command.downgrade(config, "0002")
            assert "pipeline_job_logs" not in inspect(connection).get_table_names(schema=schema)
            assert connection.scalar(select(func.count()).select_from(PipelineJob)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineJobStep)) == 1
            command.upgrade(config, "head")
            assert connection.scalar(select(func.count()).select_from(PipelineJobLog)) == 0

            command.downgrade(config, "0001")
            assert set(inspect(connection).get_table_names(schema=schema)) == {"alembic_version", "pipeline_runs"}
            assert connection.scalar(select(PipelineRun.workflow_name).where(PipelineRun.id == run_id)) == "Existing run"

            command.upgrade(config, "head")
            assert connection.scalar(select(func.count()).select_from(PipelineRun)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineJob)) == 0
            assert connection.scalar(select(func.count()).select_from(PipelineJobStep)) == 0
        finally:
            transaction.rollback()
