from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import PipelineJob, PipelineJobStep, PipelineRun


def add_run(session: Session, attempt: int = 1) -> PipelineRun:
    run = PipelineRun(
        repository="example/project", github_run_id=8000, run_attempt=attempt,
        workflow_name="Build", status="completed", conclusion="failure",
        created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
    )
    session.add(run)
    session.flush()
    return run


def add_job(session: Session, run_id: int, github_job_id: int = 9000) -> PipelineJob:
    job = PipelineJob(
        pipeline_run_id=run_id, github_job_id=github_job_id, name="Build (linux)", status="queued",
    )
    session.add(job)
    session.flush()
    return job


def add_step(session: Session, job_id: int, number: int = 1) -> PipelineJobStep:
    step = PipelineJobStep(
        pipeline_job_id=job_id, step_number=number, name="Run tests", status="queued",
    )
    session.add(step)
    session.flush()
    return step


def test_job_and_step_round_trip_aware_source_timestamps(db_session: Session) -> None:
    run = add_run(db_session)
    job = add_job(db_session, run.id)
    step = add_step(db_session, job.id)
    started = datetime(2026, 11, 7, 15, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    completed = started + timedelta(minutes=2)
    for record in (job, step):
        record.status = "completed"
        record.conclusion = "failure"
        record.started_at = started
        record.completed_at = completed
    db_session.flush()
    job_id, step_id = job.id, step.id
    db_session.expunge_all()

    saved_job = db_session.get(PipelineJob, job_id)
    saved_step = db_session.get(PipelineJobStep, step_id)
    assert saved_job.pipeline_run_id == run.id
    assert saved_job.github_job_id == 9000
    assert saved_step.pipeline_job_id == job_id
    assert saved_step.step_number == 1
    for saved in (saved_job, saved_step):
        assert saved.status == "completed"
        assert saved.conclusion == "failure"
        assert saved.started_at == started
        assert saved.completed_at == completed
        assert saved.started_at.utcoffset() is not None
        assert saved.completed_at.utcoffset() is not None
        assert saved.ingested_at.utcoffset() is not None


def test_unfinished_evidence_keeps_unknown_results_and_timestamps_null(db_session: Session) -> None:
    job = add_job(db_session, add_run(db_session).id)
    step = add_step(db_session, job.id)
    db_session.refresh(job)
    db_session.refresh(step)
    for record in (job, step):
        assert record.status == "queued"
        assert record.conclusion is None
        assert record.started_at is None
        assert record.completed_at is None


def test_job_and_step_identity_is_scoped_to_parent(db_session: Session) -> None:
    first_run = add_run(db_session)
    second_run = add_run(db_session, attempt=2)
    first_job = add_job(db_session, first_run.id)
    second_job = add_job(db_session, second_run.id)
    # Names and step ordinals can repeat in different jobs; ordinal gaps are valid.
    steps = [add_step(db_session, job.id, number) for job in (first_job, second_job) for number in (1, 7)]
    assert first_job.id != second_job.id
    assert len({step.id for step in steps}) == 4
    assert {step.name for step in steps} == {"Run tests"}

    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            add_job(db_session, first_run.id)
    assert error.value.orig.diag.constraint_name == "uq_pipeline_jobs_run_job"

    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            add_step(db_session, first_job.id, 1)
    assert error.value.orig.diag.constraint_name == "uq_pipeline_job_steps_job_number"


@pytest.mark.parametrize("parent", ["run", "job"])
def test_evidence_requires_an_existing_parent(db_session: Session, parent: str) -> None:
    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            if parent == "run":
                add_job(db_session, 9_223_372_036_854_775_807)
            else:
                add_step(db_session, 9_223_372_036_854_775_807)
    expected = "fk_pipeline_jobs_run" if parent == "run" else "fk_pipeline_job_steps_job"
    assert error.value.orig.diag.constraint_name == expected


@pytest.mark.parametrize("value", [0, -1])
@pytest.mark.parametrize("field", ["github_job_id", "step_number"])
def test_source_identifiers_must_be_positive(db_session: Session, field: str, value: int) -> None:
    run = add_run(db_session)
    job = add_job(db_session, run.id)
    with pytest.raises(IntegrityError) as error:
        with db_session.begin_nested():
            if field == "github_job_id":
                add_job(db_session, run.id, value)
            else:
                add_step(db_session, job.id, value)
    expected = (
        "ck_pipeline_jobs_github_id_positive"
        if field == "github_job_id" else "ck_pipeline_job_steps_number_positive"
    )
    assert error.value.orig.diag.constraint_name == expected


def test_deleting_a_run_cascades_only_its_own_evidence(db_session: Session) -> None:
    first_run = add_run(db_session)
    second_run = add_run(db_session, attempt=2)
    first_job = add_job(db_session, first_run.id)
    second_job = add_job(db_session, second_run.id)
    add_step(db_session, first_job.id)
    second_step = add_step(db_session, second_job.id)

    # Execute a database DELETE, so this verifies foreign-key cascades rather than ORM cleanup.
    db_session.execute(delete(PipelineRun).where(PipelineRun.id == first_run.id))
    assert list(db_session.scalars(select(PipelineRun.id))) == [second_run.id]
    assert list(db_session.scalars(select(PipelineJob.id))) == [second_job.id]
    assert list(db_session.scalars(select(PipelineJobStep.id))) == [second_step.id]


def test_deleting_a_job_cascades_steps_and_preserves_the_run(db_session: Session) -> None:
    run = add_run(db_session)
    job = add_job(db_session, run.id)
    add_step(db_session, job.id)
    db_session.execute(delete(PipelineJob).where(PipelineJob.id == job.id))
    assert list(db_session.scalars(select(PipelineJobStep.id))) == []
    assert list(db_session.scalars(select(PipelineJob.id))) == []
    assert list(db_session.scalars(select(PipelineRun.id))) == [run.id]
