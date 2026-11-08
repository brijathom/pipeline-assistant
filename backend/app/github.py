"""Read-only, bounded access to public GitHub Actions runs, jobs, and log excerpts."""
import re
from urllib.parse import urlsplit

import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError

from app.log_processing import MAX_INPUT_BYTES, LogExcerpt, LogProcessingError, process_log
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

    _check_status(response)
    return response


def _check_status(response: httpx.Response, *, expected: int = 200) -> None:
    if response.status_code == expected:
        return
    if response.status_code == 429 or (
        response.status_code == 403
        and (response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers)
    ):
        raise GitHubError("GitHub rate limit reached. Wait before retrying.")
    if response.status_code == 404:
        raise GitHubError("Public repository or Actions data not found. Check the repository, run ID and attempt.")
    if response.is_redirect:
        raise GitHubError("GitHub redirected this repository. Configure its current owner/name.")
    raise GitHubError(f"GitHub API request failed (HTTP {response.status_code}).")


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


def _log_download_url(location: str) -> str:
    # Only accept a Location obtained directly from GitHub's log endpoint, never
    # a caller-supplied URL. Blob storage is shared infrastructure, not proof of
    # GitHub ownership; the trusted API response is part of this boundary.
    try:
        url = urlsplit(location)
        host = url.hostname or ""
        allowed_host = re.fullmatch(
            r"(?:[a-z0-9-]+\.)+actions\.githubusercontent\.com|[a-z0-9]{3,24}\.blob\.core\.windows\.net",
            host,
        )
        if (
            not location or len(location) > 8192
            or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in location)
            or "\\" in location or url.scheme != "https" or not allowed_host
            or url.port not in (None, 443) or url.username is not None
            or url.password is not None or url.fragment
        ):
            raise ValueError
    except ValueError:
        raise GitHubError("GitHub returned an unsupported log download URL.") from None
    return location


def fetch_job_log_excerpt(
    repository: str,
    job_id: int,
    *,
    start_line: int = 1,
    transport: httpx.BaseTransport | None = None,
) -> LogExcerpt:
    """Fetch one public job log into memory and return only a processed excerpt.

    No authentication, retries, disk writes, or automatic redirects. The only
    allowed redirect is GitHub's initial 302 to a validated HTTPS log host.
    Raw logs and signed URLs must never be persisted or included in diagnostics.
    """
    repository = normalize_repository(repository)
    if type(job_id) is not int or not 1 <= job_id <= 9_223_372_036_854_775_807:
        raise ValueError("GitHub job ID must be a positive PostgreSQL bigint")
    if type(start_line) is not int or start_line < 1:
        raise ValueError("start_line must be a positive integer")
    try:
        with _client(transport) as client:
            try:
                metadata = _Repository.model_validate(_get(client, f"repos/{repository}").json())
            except (ValidationError, ValueError):
                raise GitHubError("GitHub returned an invalid repository response.") from None
            if metadata.private:
                raise GitHubError("Only public repositories are supported by this importer.")
            # Stream even the redirect/error response so its body is never buffered.
            with client.stream("GET", f"repos/{repository}/actions/jobs/{job_id}/logs") as response:
                _check_status(response, expected=302)
                location = _log_download_url(response.headers.get("location", ""))

        # Separate client: no API headers or cookies accompany the signed URL.
        with httpx.Client(
            headers={"Accept": "text/plain", "Accept-Encoding": "identity", "User-Agent": "Pipeline-Assistant/0.1"},
            timeout=httpx.Timeout(10.0, connect=5.0),
            follow_redirects=False,
            transport=transport,
        ) as downloader:
            with downloader.stream("GET", location) as response:
                if response.status_code != 200:
                    raise GitHubError(f"Log download failed (HTTP {response.status_code}). Request a fresh download.")
                # Reject compression rather than buffering an unbounded expansion.
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise GitHubError("Compressed log downloads are not supported.")
                length = response.headers.get("content-length")
                if length is not None and (len(length) > 20 or not length.isascii() or not length.isdecimal()):
                    raise GitHubError("Log download returned an invalid content length.")
                if length is not None and int(length) > MAX_INPUT_BYTES:
                    raise GitHubError("Log download exceeds the 1 MiB processing limit.")
                raw = bytearray()
                for chunk in response.iter_raw(chunk_size=8192):
                    if len(raw) + len(chunk) > MAX_INPUT_BYTES:
                        raise GitHubError("Log download exceeds the 1 MiB processing limit.")
                    raw.extend(chunk)
                if length is not None and len(raw) != int(length):
                    raise GitHubError("Log download was incomplete. Request a fresh download.")
    except httpx.RequestError:
        # Transport exceptions can contain the signed URL or other sensitive data.
        raise GitHubError("Could not download GitHub logs. Check your connection and retry.") from None
    try:
        return process_log(bytes(raw), start_line=start_line)
    except LogProcessingError:
        raise GitHubError("Log content could not be processed. Check UTF-8 encoding and the requested source line.") from None
