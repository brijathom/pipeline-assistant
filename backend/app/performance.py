"""Job execution percentiles for UTC cohorts of stored workflow attempts."""
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from sqlalchemy import Date, Double, case, cast, func, select
from sqlalchemy.orm import Session

from app.models import PipelineJob, PipelineRun
from app.schemas import JobDurationDay, PipelinePerformanceResponse


class PerformanceWindow(IntEnum):
    week = 7
    month = 30
    quarter = 90


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def job_performance(session: Session, days: int, repository: str | None, now: datetime) -> PipelinePerformanceResponse:
    """One grouped SQL aggregate; missing/invalid durations remain NULL, never zero."""
    now = now.astimezone(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days - 1)
    valid = (
        (PipelineJob.status == "completed")
        & PipelineJob.conclusion.is_distinct_from("skipped")
        & PipelineJob.started_at.is_not(None) & PipelineJob.completed_at.is_not(None)
        & (PipelineJob.started_at >= PipelineRun.created_at)
        & (PipelineJob.completed_at >= PipelineJob.started_at)
        & (PipelineJob.completed_at <= now)
    )
    duration = case((valid, cast(func.extract("epoch", PipelineJob.completed_at - PipelineJob.started_at), Double)))
    source = select(
        cast(func.timezone("UTC", PipelineRun.created_at), Date).label("day"),
        PipelineJob.id.label("job_id"), duration.label("duration"),
    ).join(PipelineRun, PipelineRun.id == PipelineJob.pipeline_run_id).where(
        PipelineRun.created_at >= start, PipelineRun.created_at < now,
    )
    if repository is not None:
        source = source.where(PipelineRun.repository == repository)
    jobs = source.subquery()
    # ROLLUP returns daily groups and one overall group in the same SQL snapshot.
    # Overall percentiles use individual jobs, never an average of daily medians.
    rows = session.execute(select(
        jobs.c.day, func.count(jobs.c.job_id).label("total_jobs"), func.count(jobs.c.duration).label("timed_jobs"),
        func.percentile_cont(0.5).within_group(jobs.c.duration).label("median_job_duration_seconds"),
        func.percentile_cont(0.95).within_group(jobs.c.duration).label("p95_job_duration_seconds"),
    ).group_by(func.rollup(jobs.c.day))).mappings().all()
    summary = next(row for row in rows if row["day"] is None)
    by_date = {row["day"]: row for row in rows if row["day"] is not None}

    def values(row):
        total, timed = row["total_jobs"], row["timed_jobs"]
        return dict(total_jobs=total, timed_jobs=timed, excluded_jobs=total - timed,
            median_job_duration_seconds=row["median_job_duration_seconds"],
            p95_job_duration_seconds=row["p95_job_duration_seconds"])

    empty = dict(total_jobs=0, timed_jobs=0, excluded_jobs=0, median_job_duration_seconds=None, p95_job_duration_seconds=None)
    daily = []
    for offset in range(days):
        day = start.date() + timedelta(days=offset)
        daily.append(JobDurationDay(date=day, **(values(by_date[day]) if day in by_date else empty)))
    return PipelinePerformanceResponse(repository=repository, days=days, start_at=start, end_at=now,
        daily=daily, **values(summary))
