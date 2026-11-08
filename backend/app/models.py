from datetime import datetime

from sqlalchemy import (
    BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Identity, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"
    __table_args__ = (
        UniqueConstraint("repository", "github_run_id", "run_attempt", name="uq_pipeline_runs_attempt"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    repository: Mapped[str] = mapped_column(String(255))
    github_run_id: Mapped[int] = mapped_column(BigInteger)
    run_attempt: Mapped[int] = mapped_column(server_default="1")
    workflow_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(50))
    conclusion: Mapped[str | None] = mapped_column(String(50))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"
    __table_args__ = (
        UniqueConstraint("pipeline_run_id", "github_job_id", name="uq_pipeline_jobs_run_job"),
        CheckConstraint("github_job_id > 0", name="ck_pipeline_jobs_github_id_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pipeline_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", name="fk_pipeline_jobs_run", ondelete="CASCADE"),
    )
    github_job_id: Mapped[int] = mapped_column(BigInteger)
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50))
    conclusion: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineJobStep(Base):
    __tablename__ = "pipeline_job_steps"
    __table_args__ = (
        UniqueConstraint("pipeline_job_id", "step_number", name="uq_pipeline_job_steps_job_number"),
        CheckConstraint("step_number > 0", name="ck_pipeline_job_steps_number_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    pipeline_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_jobs.id", name="fk_pipeline_job_steps_job", ondelete="CASCADE"),
    )
    step_number: Mapped[int] = mapped_column()
    name: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(50))
    conclusion: Mapped[str | None] = mapped_column(String(50))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineJobLog(Base):
    __tablename__ = "pipeline_job_logs"
    __table_args__ = (
        CheckConstraint("octet_length(text) <= 16384", name="ck_pipeline_job_logs_bytes"),
        CheckConstraint("line_count BETWEEN 0 AND 100", name="ck_pipeline_job_logs_lines"),
        CheckConstraint(
            "start_line >= 1 AND total_source_lines >= 0 AND start_line + line_count - 1 <= total_source_lines",
            name="ck_pipeline_job_logs_source_range",
        ),
        CheckConstraint("source_kind IN ('github', 'demo')", name="ck_pipeline_job_logs_source_kind"),
    )

    pipeline_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_jobs.id", name="fk_pipeline_job_logs_job", ondelete="CASCADE"),
        primary_key=True,
    )
    text: Mapped[str] = mapped_column(Text)
    start_line: Mapped[int] = mapped_column()
    line_count: Mapped[int] = mapped_column()
    total_source_lines: Mapped[int] = mapped_column()
    truncated: Mapped[bool] = mapped_column(Boolean)
    redactions_applied: Mapped[bool] = mapped_column(Boolean)
    source_kind: Mapped[str] = mapped_column(String(20))
    is_curated: Mapped[bool] = mapped_column(Boolean, server_default="false")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PipelineInvestigation(Base):
    __tablename__ = "pipeline_investigations"
    __table_args__ = (
        CheckConstraint("length(evidence_fingerprint) = 64", name="ck_pipeline_investigations_fingerprint"),
        CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object' AND octet_length(evidence_snapshot::text) <= 524288",
            name="ck_pipeline_investigations_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(report) = 'object' AND octet_length(report::text) <= 524288",
            name="ck_pipeline_investigations_report",
        ),
    )

    pipeline_run_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("pipeline_runs.id", name="fk_pipeline_investigations_run", ondelete="CASCADE"),
        primary_key=True,
    )
    generation_method: Mapped[str] = mapped_column(String(50))
    evidence_fingerprint: Mapped[str] = mapped_column(String(64))
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB)
    report: Mapped[dict] = mapped_column(JSONB)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
