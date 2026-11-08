"""One inspected public CI excerpt, retained with source provenance."""

# Retrieved through the repository owner's existing GitHub CLI connection.
# Only this processed test-summary line was retained; raw logs were not saved.
# It contains no credentials, personal information or instructions to execute.
GITHUB_SUCCESS_SAMPLE = {
    "repository": "brijathom/pipeline-assistant",
    "github_run_id": 42484550107,
    "run_attempt": 1,
    "github_job_id": 128743448857,
    "job_name": "Backend tests",
    "status": "completed",
    "conclusion": "success",
    "source_url": "https://github.com/brijathom/pipeline-assistant/actions/runs/42484550107/job/128743448857",
    "source_line": 561,
    "text": "2026-11-08T13:22:05.2119226Z ======================== 484 passed, 1 warning in 6.34s ========================",
    "total_source_lines": 1055,
    "truncated": True,
    "source_kind": "github",
    "captured_on": "2026-11-08",
}
