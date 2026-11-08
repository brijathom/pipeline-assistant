"""Create durable investigation tasks.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "investigation_tasks",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(100), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("draft_id", sa.Uuid()),
        sa.Column("error_code", sa.String(40)),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], name="fk_investigation_tasks_run", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["draft_id"], ["pipeline_investigation_drafts.id"], name="fk_investigation_tasks_draft", ondelete="SET NULL"),
        sa.CheckConstraint("status IN ('queued', 'running', 'succeeded', 'failed')", name="ck_investigation_tasks_status"),
        sa.CheckConstraint("evidence_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_investigation_tasks_fingerprint"),
        sa.CheckConstraint("jsonb_typeof(evidence_snapshot) = 'object' AND octet_length(evidence_snapshot::text) <= 65536", name="ck_investigation_tasks_snapshot"),
        sa.CheckConstraint(
            "(status = 'queued' AND started_at IS NULL AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL AND error_code IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL AND error_code IS NOT NULL)",
            name="ck_investigation_tasks_lifecycle",
        ),
        sa.CheckConstraint("status = 'succeeded' OR draft_id IS NULL", name="ck_investigation_tasks_result"),
        sa.CheckConstraint("error_code IN ('evidence_changed', 'invalid_snapshot', 'generation_failed', 'worker_interrupted')", name="ck_investigation_tasks_error"),
    )
    op.create_index("ix_investigation_tasks_pipeline_run_id", "investigation_tasks", ["pipeline_run_id"])
    op.create_index("uq_investigation_tasks_active_run", "investigation_tasks", ["pipeline_run_id"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))


def downgrade() -> None:
    op.drop_table("investigation_tasks")
