from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.schema import CreateSchema

from app.models import InvestigationTask, PipelineInvestigation, PipelineInvestigationDraft, PipelineJob, PipelineJobLog, PipelineJobStep, PipelineRun


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
            connection.execute(PipelineInvestigation.__table__.insert().values(
                pipeline_run_id=run_id, generation_method="rules-v1", evidence_fingerprint="a" * 64,
                evidence_snapshot={}, report={},
            ))
            connection.execute(PipelineInvestigationDraft.__table__.insert().values(
                id=uuid4(), pipeline_run_id=run_id, content_fingerprint="b" * 64,
                evidence_fingerprint="a" * 64, document={"report": {}},
            ))
            task_id = uuid4()
            connection.execute(InvestigationTask.__table__.insert().values(
                id=task_id, pipeline_run_id=run_id, model="gemini-3.5-flash-lite", prompt_version="investigation-v1",
                evidence_fingerprint="a" * 64, evidence_snapshot={},
            ))
            connection.execute(text("UPDATE investigation_tasks SET dispatch_attempts = 2 WHERE id = :task_id"), {"task_id": task_id})
            command.downgrade(config, "0007")
            assert connection.scalar(text("SELECT count(*) FROM investigation_tasks")) == 1
            assert "dispatch_attempts" not in {column["name"] for column in inspect(connection).get_columns("investigation_tasks", schema=schema)}
            command.upgrade(config, "head")
            assert connection.scalar(select(InvestigationTask.dispatch_attempts).where(InvestigationTask.id == task_id)) == 0
            command.downgrade(config, "0006")
            assert "investigation_tasks" not in inspect(connection).get_table_names(schema=schema)
            assert connection.scalar(select(func.count()).select_from(PipelineRun)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigation)) == 1
            command.upgrade(config, "head")
            assert connection.scalar(select(func.count()).select_from(InvestigationTask)) == 0
            command.downgrade(config, "0004")
            assert "pipeline_investigation_drafts" not in inspect(connection).get_table_names(schema=schema)
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigation)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineJobLog)) == 1
            command.upgrade(config, "head")
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 0
            command.downgrade(config, "0003")
            assert "pipeline_investigations" not in inspect(connection).get_table_names(schema=schema)
            assert connection.scalar(select(func.count()).select_from(PipelineJobLog)) == 1
            command.upgrade(config, "head")
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigation)) == 0
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
            # Rolling back publication support keeps the private approved source
            # and the run, while removing the public AI copy old code cannot read.
            draft_id = uuid4()
            connection.execute(PipelineInvestigationDraft.__table__.insert().values(
                id=draft_id, pipeline_run_id=run_id, content_fingerprint="c" * 64,
                evidence_fingerprint="a" * 64, document={}, status="approved",
                reviewed_at=datetime(2026, 11, 8, tzinfo=timezone.utc), reviewed_by="migration-test", review_note="Fixture",
            ))
            connection.execute(PipelineInvestigation.__table__.insert().values(
                pipeline_run_id=run_id, generation_method="google-adk-v1", evidence_fingerprint="a" * 64,
                evidence_snapshot={}, report={}, source_draft_id=draft_id,
            ))
            command.downgrade(config, "0005")
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigation)) == 0
            assert connection.scalar(select(func.count()).select_from(PipelineInvestigationDraft)) == 1
            assert connection.scalar(select(func.count()).select_from(PipelineRun)) == 1
            assert "source_draft_id" not in {column["name"] for column in inspect(connection).get_columns("pipeline_investigations", schema=schema)}
            command.upgrade(config, "head")
        finally:
            transaction.rollback()
