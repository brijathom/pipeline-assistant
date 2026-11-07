from datetime import datetime

from sqlalchemy import (
    BigInteger, CheckConstraint, DateTime, ForeignKey, Identity, String, Text,
    UniqueConstraint, func,
)
from sqlalchemy.orm import Mapped, mapped_column

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
