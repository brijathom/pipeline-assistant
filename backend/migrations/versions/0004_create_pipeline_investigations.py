"""Store evidence-backed investigation reports.

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_investigations",
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("generation_method", sa.String(50), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(), nullable=False),
        sa.Column("report", postgresql.JSONB(), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("pipeline_run_id"),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], name="fk_pipeline_investigations_run", ondelete="CASCADE",
        ),
        sa.CheckConstraint("length(evidence_fingerprint) = 64", name="ck_pipeline_investigations_fingerprint"),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object' AND octet_length(evidence_snapshot::text) <= 524288",
            name="ck_pipeline_investigations_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(report) = 'object' AND octet_length(report::text) <= 524288",
            name="ck_pipeline_investigations_report",
        ),
    )


def downgrade() -> None:
    op.drop_table("pipeline_investigations")
