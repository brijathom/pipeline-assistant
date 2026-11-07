"""Create pipeline runs.

Revision ID: 0001
Revises: None
"""
from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("repository", sa.String(255), nullable=False),
        sa.Column("github_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_attempt", sa.Integer(), server_default="1", nullable=False),
        sa.Column("workflow_name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("conclusion", sa.String(50), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("repository", "github_run_id", "run_attempt", name="uq_pipeline_runs_attempt"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_runs")
