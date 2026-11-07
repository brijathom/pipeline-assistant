"""Create pipeline jobs and steps.

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("github_job_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("conclusion", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], name="fk_pipeline_jobs_run", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("pipeline_run_id", "github_job_id", name="uq_pipeline_jobs_run_job"),
        sa.CheckConstraint("github_job_id > 0", name="ck_pipeline_jobs_github_id_positive"),
    )
    op.create_table(
        "pipeline_job_steps",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("pipeline_job_id", sa.BigInteger(), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(50), nullable=False),
        sa.Column("conclusion", sa.String(50), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["pipeline_job_id"], ["pipeline_jobs.id"], name="fk_pipeline_job_steps_job", ondelete="CASCADE",
        ),
        sa.UniqueConstraint("pipeline_job_id", "step_number", name="uq_pipeline_job_steps_job_number"),
        sa.CheckConstraint("step_number > 0", name="ck_pipeline_job_steps_number_positive"),
    )


def downgrade() -> None:
    op.drop_table("pipeline_job_steps")
    op.drop_table("pipeline_jobs")
