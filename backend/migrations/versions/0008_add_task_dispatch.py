"""Persist bounded Pub/Sub dispatch attempts alongside tasks.

Revision ID: 0008
Revises: 0007
"""
from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("investigation_tasks", sa.Column("dispatch_attempts", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("investigation_tasks", sa.Column("dispatch_after", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()))
    op.add_column("investigation_tasks", sa.Column("dispatched_at", sa.DateTime(timezone=True)))
    op.create_check_constraint("ck_investigation_tasks_dispatch_attempts", "investigation_tasks", "dispatch_attempts BETWEEN 0 AND 5")
    op.create_index("ix_investigation_tasks_dispatch", "investigation_tasks", ["dispatch_after"],
                    postgresql_where=sa.text("status = 'queued' AND dispatched_at IS NULL AND dispatch_attempts < 5"))


def downgrade() -> None:
    op.drop_index("ix_investigation_tasks_dispatch", table_name="investigation_tasks")
    op.drop_constraint("ck_investigation_tasks_dispatch_attempts", "investigation_tasks", type_="check")
    op.drop_column("investigation_tasks", "dispatched_at")
    op.drop_column("investigation_tasks", "dispatch_after")
    op.drop_column("investigation_tasks", "dispatch_attempts")
