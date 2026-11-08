from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.evaluation_cases import evaluation_cases
from app.evaluation_samples import GITHUB_SUCCESS_SAMPLE
from app.investigations import build_report, collect_evidence
from app.log_evidence import save_log_excerpt
from app.log_processing import LogExcerpt, LogLine
from app.models import PipelineJob, PipelineRun


def test_real_successful_ci_summary_is_excluded_from_failure_evidence(db_session: Session) -> None:
    sample = GITHUB_SUCCESS_SAMPLE
    run = PipelineRun(
        repository=sample["repository"], github_run_id=sample["github_run_id"], run_attempt=sample["run_attempt"],
        workflow_name="CI", status=sample["status"], conclusion=sample["conclusion"],
        created_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
    )
    db_session.add(run)
    db_session.flush()
    job = PipelineJob(pipeline_run_id=run.id, github_job_id=sample["github_job_id"],
        name=sample["job_name"], status=sample["status"], conclusion=sample["conclusion"])
    db_session.add(job)
    db_session.flush()
    excerpt = LogExcerpt((LogLine(sample["source_line"], sample["text"]),),
        sample["total_source_lines"], sample["truncated"], False)
    save_log_excerpt(db_session, job.id, excerpt, source_kind="github", is_curated=True)
    bundle = collect_evidence(db_session, run)
    assert bundle.failed_job_count == 0 and bundle.jobs == []
    assert build_report(bundle).findings == []
    expected = next(case for case in evaluation_cases() if case.id == "github-success-excluded").evidence
    assert bundle.model_dump(exclude={"run_id"}) == expected.model_dump(exclude={"run_id"})
