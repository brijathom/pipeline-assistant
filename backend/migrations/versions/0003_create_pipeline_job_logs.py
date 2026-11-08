"""Store bounded, processed job-log excerpts.

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_job_logs",
        sa.Column("pipeline_job_id", sa.BigInteger(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=False),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("total_source_lines", sa.Integer(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("redactions_applied", sa.Boolean(), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("is_curated", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("pipeline_job_id"),
        sa.ForeignKeyConstraint(
            ["pipeline_job_id"], ["pipeline_jobs.id"], name="fk_pipeline_job_logs_job", ondelete="CASCADE",
        ),
        sa.CheckConstraint("octet_length(text) <= 16384", name="ck_pipeline_job_logs_bytes"),
        sa.CheckConstraint("line_count BETWEEN 0 AND 100", name="ck_pipeline_job_logs_lines"),
        sa.CheckConstraint(
            "start_line >= 1 AND total_source_lines >= 0 AND start_line + line_count - 1 <= total_source_lines",
            name="ck_pipeline_job_logs_source_range",
        ),
        sa.CheckConstraint("source_kind IN ('github', 'demo')", name="ck_pipeline_job_logs_source_kind"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_job_logs")
