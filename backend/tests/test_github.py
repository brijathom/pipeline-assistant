import httpx
import pytest

from app.github import GitHubError, fetch_workflow_runs


@pytest.mark.parametrize("limit", [1, 2])
def test_public_import_is_bounded_and_read_only(github_runs_payload: dict, limit: int) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.github.com"
        assert "authorization" not in request.headers
        assert request.headers["x-github-api-version"] == "2026-03-10"
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        assert request.url.path == "/repos/example/project/actions/runs"
        assert request.url.params["per_page"] == str(limit)
        assert request.url.params["page"] == "1"
        return httpx.Response(
            200,
            json=github_runs_payload,
            headers={"Link": '<https://api.github.com/repos/example/project/actions/runs?page=2>; rel="next"'},
        )

    runs = fetch_workflow_runs("example/project", limit, transport=httpx.MockTransport(handle))
    assert len(requests) == 2
    assert [run.id for run in runs] == [3001, 3000][:limit]
    assert runs[0].created_at.tzinfo is not None
    assert runs[0].conclusion is None


def test_private_repository_is_rejected() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"private": True})

    with pytest.raises(GitHubError, match="Only public"):
        fetch_workflow_runs("example/project", transport=httpx.MockTransport(handle))
    assert len(requests) == 1


@pytest.mark.parametrize(("status", "headers", "message"), [
    (404, {}, "not found"),
    (403, {"x-ratelimit-remaining": "0"}, "rate limit"),
    (429, {"retry-after": "60"}, "rate limit"),
    (403, {}, "HTTP 403"),
    (503, {}, "HTTP 503"),
    (301, {"Location": "https://example.invalid/"}, "redirected"),
])
def test_api_errors_are_reported_without_retries(status: int, headers: dict, message: str) -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        return httpx.Response(status, headers=headers)

    with pytest.raises(GitHubError, match=message):
        fetch_workflow_runs("example/project", transport=httpx.MockTransport(handle))
    assert len(requests) == 2


def test_timeout_is_reported_without_internal_details() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("internal transport details", request=request)

    with pytest.raises(GitHubError, match="Could not reach GitHub") as caught:
        fetch_workflow_runs("example/project", transport=httpx.MockTransport(handle))
    assert "internal transport details" not in str(caught.value)


@pytest.mark.parametrize("problem", ["invalid_json", "missing_id", "naive_timestamp"])
def test_malformed_response_is_rejected(github_runs_payload: dict, problem: str) -> None:
    if problem == "missing_id":
        del github_runs_payload["workflow_runs"][1]["id"]
    elif problem == "naive_timestamp":
        github_runs_payload["workflow_runs"][1]["created_at"] = "2026-11-07T12:00:00"

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/example/project":
            return httpx.Response(200, json={"private": False})
        if problem == "invalid_json":
            return httpx.Response(200, text="invalid JSON")
        return httpx.Response(200, json=github_runs_payload)

    with pytest.raises(GitHubError, match="invalid repository or workflow-run response"):
        fetch_workflow_runs("example/project", transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("limit", [0, 101])
def test_invalid_limit_is_rejected_before_network_access(limit: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid limits must not contact GitHub")

    with pytest.raises(ValueError, match="between 1 and 100"):
        fetch_workflow_runs("example/project", limit, transport=httpx.MockTransport(handle))
