from copy import deepcopy

import httpx
import pytest

from app.github import GitHubError, fetch_workflow_jobs


def test_jobs_use_the_exact_attempt_and_read_only_public_requests(github_jobs_payload: dict) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert "authorization" not in request.headers
        assert request.headers["x-github-api-version"] == "2026-03-10"
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        assert request.url.path == "/repos/example/project/actions/runs/3001/attempts/2/jobs"
        assert dict(request.url.params) == {"per_page": "100", "page": "1"}
        return httpx.Response(200, json=github_jobs_payload)

    jobs = fetch_workflow_jobs("EXAMPLE/PROJECT", 3001, 2, transport=httpx.MockTransport(handle))
    assert len(requests) == 2
    assert [job.id for job in jobs] == [7001, 7002]
    assert jobs[0].completed_at.tzinfo is not None
    assert jobs[0].steps[1].number == 7
    assert jobs[1].started_at is None
    assert jobs[1].run_attempt is None  # The field is absent in some GitHub responses.


@pytest.mark.parametrize("problem", [None, "later_error", "changed_total", "duplicate", "incomplete"])
def test_pagination_is_complete_and_never_follows_link_urls(github_jobs_payload: dict, problem: str | None) -> None:
    first_page = []
    for index in range(100):
        job = deepcopy(github_jobs_payload["jobs"][0])
        job["id"] = 8000 + index
        first_page.append(job)
    last_job = deepcopy(first_page[0])
    last_job["id"] = first_page[0]["id"] if problem == "duplicate" else 9000
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host == "api.github.com"
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        if request.url.params["page"] == "1":
            return httpx.Response(200, json={"total_count": 101, "jobs": first_page}, headers={
                "Link": '<https://untrusted.invalid/collect>; rel="next"',
            })
        assert request.url.params["page"] == "2"
        if problem == "later_error":
            return httpx.Response(503)
        return httpx.Response(200, json={
            "total_count": 102 if problem == "changed_total" else 101,
            "jobs": [] if problem == "incomplete" else [last_job],
        })

    if problem:
        with pytest.raises(GitHubError):
            fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle))
    else:
        jobs = fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle))
        assert len(jobs) == 101
        assert jobs[-1].id == 9000
    assert len(requests) == 3


@pytest.mark.parametrize("problem", [
    "wrong_run", "wrong_attempt", "duplicate_step", "naive_timestamp",
    "missing_steps", "negative_total", "too_many_jobs", "invalid_json",
])
def test_invalid_job_snapshots_are_rejected(github_jobs_payload: dict, problem: str) -> None:
    job = github_jobs_payload["jobs"][0]
    if problem == "wrong_run":
        job["run_id"] = 9999
    elif problem == "wrong_attempt":
        job["run_attempt"] = 3
    elif problem == "duplicate_step":
        job["steps"][1]["number"] = 1
    elif problem == "naive_timestamp":
        job["steps"][0]["started_at"] = "2026-11-07T12:00:00"
    elif problem == "missing_steps":
        del job["steps"]
    elif problem == "negative_total":
        github_jobs_payload["total_count"] = -1
    elif problem == "too_many_jobs":
        github_jobs_payload["total_count"] = 501

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        if problem == "invalid_json":
            return httpx.Response(200, text="not JSON")
        return httpx.Response(200, json=github_jobs_payload)

    with pytest.raises(GitHubError):
        fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle))


def test_no_jobs_is_a_valid_empty_snapshot() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"private": False} if request.url.path == "/repos/example/project"
                              else {"total_count": 0, "jobs": []})

    assert fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle)) == []


def test_private_jobs_are_not_requested() -> None:
    paths = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"private": True})

    with pytest.raises(GitHubError, match="Only public"):
        fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle))
    assert paths == ["/repos/example/project"]


@pytest.mark.parametrize(("status", "headers", "message"), [
    (404, {}, "not found"), (403, {"x-ratelimit-remaining": "0"}, "rate limit"),
    (429, {}, "rate limit"), (302, {"Location": "https://untrusted.invalid"}, "redirected"),
])
def test_job_http_errors_are_safe_and_not_retried(status: int, headers: dict, message: str) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        return httpx.Response(status, headers=headers)

    with pytest.raises(GitHubError, match=message):
        fetch_workflow_jobs("example/project", 3001, 2, transport=httpx.MockTransport(handle))
    assert len(requests) == 2


@pytest.mark.parametrize(("repository", "run_id", "attempt"), [
    ("https://untrusted.invalid/", 3001, 2), ("example/project", 0, 2), ("example/project", 3001, 0),
])
def test_invalid_source_identity_never_contacts_github(repository: str, run_id: int, attempt: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid source identity must fail before network access")

    with pytest.raises(ValueError):
        fetch_workflow_jobs(repository, run_id, attempt, transport=httpx.MockTransport(handle))
