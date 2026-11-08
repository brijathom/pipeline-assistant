"""Link public AI reports to their approved private draft.

Revision ID: 0006
Revises: 0005
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pipeline_investigations", sa.Column("source_draft_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_investigations_source_draft", "pipeline_investigations", "pipeline_investigation_drafts",
        ["source_draft_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_pipeline_investigations_source_draft_id", "pipeline_investigations", ["source_draft_id"])


def downgrade() -> None:
    # Older application versions cannot serve AI reports. Their approved source
    # documents remain in the private draft table for later republication.
    op.execute("DELETE FROM pipeline_investigations WHERE generation_method = 'google-adk-v1'")
    op.drop_index("ix_pipeline_investigations_source_draft_id", table_name="pipeline_investigations")
    op.drop_constraint("fk_investigations_source_draft", "pipeline_investigations", type_="foreignkey")
    op.drop_column("pipeline_investigations", "source_draft_id")
