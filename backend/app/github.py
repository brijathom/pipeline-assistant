"""Read-only, bounded access to public GitHub Actions runs."""
import httpx
from pydantic import AwareDatetime, BaseModel, Field, ValidationError


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
        raise GitHubError("Public repository or Actions data not found. Check GITHUB_REPOSITORY.")
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

    with httpx.Client(
        base_url="https://api.github.com/",
        headers={
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
            "User-Agent": "Pipeline-Assistant/0.1",
        },
        timeout=httpx.Timeout(10.0, connect=5.0),
        follow_redirects=False,
        transport=transport,
    ) as client:
        try:
            metadata = _Repository.model_validate(_get(client, f"repos/{repository}").json())
            if metadata.private:
                raise GitHubError("Only public repositories are supported by this importer.")
            response = _get(client, f"repos/{repository}/actions/runs", per_page=limit, page=1)
            page = _RunPage.model_validate(response.json())
        except (ValidationError, ValueError):
            raise GitHubError("GitHub returned an invalid repository or workflow-run response.") from None

    return page.workflow_runs[:limit]
