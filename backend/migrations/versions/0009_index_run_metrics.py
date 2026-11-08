"""Index bounded metric windows for global and repository views.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_pipeline_runs_created_at", "pipeline_runs", ["created_at"])
    op.create_index("ix_pipeline_runs_repository_created_at", "pipeline_runs", ["repository", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_pipeline_runs_repository_created_at", table_name="pipeline_runs")
    op.drop_index("ix_pipeline_runs_created_at", table_name="pipeline_runs")
