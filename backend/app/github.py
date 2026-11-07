"""Read-only, bounded access to public GitHub Actions runs and jobs."""
import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from app.repositories import normalize_repository

JOBS_PER_PAGE = 100
MAX_JOB_PAGES = 5


class GitHubError(Exception):
    """A safe, user-facing error from the GitHub boundary."""


class GitHubWorkflowRun(BaseModel):
    id: int = Field(gt=0)
    run_attempt: int = Field(ge=1)
    name: str | None = Field(max_length=255)
    status: str = Field(min_length=1, max_length=50)
    conclusion: str | None = Field(max_length=50)
    created_at: AwareDatetime
    run_started_at: AwareDatetime | None


class _Repository(BaseModel):
    private: bool


class _RunPage(BaseModel):
    workflow_runs: list[GitHubWorkflowRun] = Field(max_length=100)


class GitHubJobStep(BaseModel):
    number: int = Field(gt=0, le=2_147_483_647)
    name: str = Field(min_length=1, max_length=10_000)
    status: str = Field(min_length=1, max_length=50)
    conclusion: str | None = Field(max_length=50)
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None


class GitHubJob(BaseModel):
    id: int = Field(gt=0, le=9_223_372_036_854_775_807)
    run_id: int = Field(gt=0)
    run_attempt: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=10_000)
    status: str = Field(min_length=1, max_length=50)
    conclusion: str | None = Field(max_length=50)
    started_at: AwareDatetime | None
    completed_at: AwareDatetime | None
    steps: list[GitHubJobStep] = Field(max_length=1000)


class _JobPage(BaseModel):
    total_count: int = Field(ge=0)
    jobs: list[GitHubJob] = Field(max_length=JOBS_PER_PAGE)


def _client(transport: httpx.BaseTransport | None) -> httpx.Client:
    return httpx.Client(
        base_url="https://api.github.com/",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Pipeline-Assistant/0.1",
        },
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
        transport=transport,
    )


def _get(client: httpx.Client, path: str, **params: int) -> httpx.Response:
    try:
        response = client.get(path, params=params)
    except httpx.RequestError:
        raise GitHubError("Could not reach GitHub. Check your connection and try again.") from None

    if response.status_code == 429 or (
        response.status_code == 403
        and (response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers)
    ):
        raise GitHubError("GitHub rate limit reached. Wait before retrying.")
    if response.status_code == 404:
        raise GitHubError("Public repository or Actions data not found. Check the repository, run ID and attempt.")
    if response.is_redirect:
        raise GitHubError("GitHub redirected this repository. Configure its current owner/name.")
    if response.status_code != 200:
        raise GitHubError(f"GitHub API request failed (HTTP {response.status_code}).")
    return response


def fetch_workflow_runs(
    repository: str,
    limit: int = 20,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[GitHubWorkflowRun]:
    """Fetch one page, without authentication, redirects, or automatic retries.

    repository must come from the validated Settings.github_repository field.
    transport allows tests to exercise HTTP behavior without contacting GitHub.
    """
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    with _client(transport) as client:
        try:
            metadata = _Repository.model_validate(_get(client, f"repos/{repository}").json())
            if metadata.private:
                raise GitHubError("Only public repositories are supported by this importer.")
            response = _get(client, f"repos/{repository}/actions/runs", per_page=limit, page=1)
            page = _RunPage.model_validate(response.json())
        except (ValidationError, ValueError):
            raise GitHubError("GitHub returned an invalid repository or workflow-run response.") from None

    return page.workflow_runs[:limit]


def fetch_workflow_jobs(
    repository: str,
    run_id: int,
    run_attempt: int,
    *,
    transport: httpx.BaseTransport | None = None,
) -> list[GitHubJob]:
    """Fetch a complete attempt snapshot, or fail; never follow pagination URLs."""
    repository = normalize_repository(repository)
    if not 1 <= run_id <= 9_223_372_036_854_775_807 or run_attempt < 1:
        raise ValueError("GitHub run ID and attempt must be positive")
    jobs: list[GitHubJob] = []
    seen_ids: set[int] = set()
    expected_total: int | None = None
    path = f"repos/{repository}/actions/runs/{run_id}/attempts/{run_attempt}/jobs"
    with _client(transport) as client:
        try:
            metadata = _Repository.model_validate(_get(client, f"repos/{repository}").json())
            if metadata.private:
                raise GitHubError("Only public repositories are supported by this importer.")
            for page_number in range(1, MAX_JOB_PAGES + 1):
                page = _JobPage.model_validate(
                    _get(client, path, per_page=JOBS_PER_PAGE, page=page_number).json(),
                )
                if page.total_count > JOBS_PER_PAGE * MAX_JOB_PAGES:
                    raise GitHubError("This attempt exceeds the 500-job import limit. No jobs were saved.")
                if expected_total is None:
                    expected_total = page.total_count
                if page.total_count != expected_total:
                    raise GitHubError("GitHub job data changed during pagination. Retry the import.")
                expected_page_size = min(JOBS_PER_PAGE, expected_total - len(jobs))
                if len(page.jobs) != expected_page_size:
                    raise GitHubError("GitHub returned an incomplete job page. No jobs were saved.")
                for job in page.jobs:
                    if job.run_id != run_id or job.run_attempt not in (None, run_attempt):
                        raise GitHubError("GitHub returned jobs for a different run or attempt.")
                    if job.id in seen_ids:
                        raise GitHubError("GitHub returned duplicate jobs. Retry the import.")
                    if len({step.number for step in job.steps}) != len(job.steps):
                        raise GitHubError("GitHub returned duplicate step numbers for a job.")
                    seen_ids.add(job.id)
                jobs.extend(page.jobs)
                if len(jobs) == expected_total:
                    return jobs
        except (ValidationError, ValueError):
            raise GitHubError("GitHub returned an invalid repository or job response.") from None
    raise GitHubError("Could not fetch the complete job snapshot. No jobs were saved.")
