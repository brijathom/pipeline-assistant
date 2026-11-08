"""Store private investigation drafts and local review decisions.

Revision ID: 0005
Revises: 0004
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pipeline_investigation_drafts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("content_fingerprint", sa.String(64), nullable=False),
        sa.Column("evidence_fingerprint", sa.String(64), nullable=False),
        sa.Column("document", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), server_default="pending_review", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_by", sa.String(100), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], name="fk_investigation_drafts_run", ondelete="CASCADE"),
        sa.UniqueConstraint("content_fingerprint", name="uq_investigation_drafts_content"),
        sa.CheckConstraint("content_fingerprint ~ '^[0-9a-f]{64}$' AND evidence_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_investigation_drafts_fingerprints"),
        sa.CheckConstraint("jsonb_typeof(document) = 'object' AND octet_length(document::text) <= 1048576", name="ck_investigation_drafts_document"),
        sa.CheckConstraint("status IN ('pending_review', 'approved', 'rejected')", name="ck_investigation_drafts_status"),
        sa.CheckConstraint(
            "(status = 'pending_review' AND reviewed_at IS NULL AND reviewed_by IS NULL AND review_note IS NULL) OR "
            "(status IN ('approved', 'rejected') AND reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND review_note IS NOT NULL "
            "AND length(trim(reviewed_by)) BETWEEN 1 AND 100 AND length(trim(review_note)) BETWEEN 1 AND 1000)",
            name="ck_investigation_drafts_review",
        ),
    )
    op.create_index("ix_pipeline_investigation_drafts_pipeline_run_id", "pipeline_investigation_drafts", ["pipeline_run_id"])


def downgrade() -> None:
    op.drop_table("pipeline_investigation_drafts")
