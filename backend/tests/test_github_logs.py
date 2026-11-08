from collections.abc import Iterator

import httpx
import pytest

from app.github import GitHubError, fetch_job_log_excerpt
from app.log_processing import MAX_INPUT_BYTES

DOWNLOAD_URL = "https://productionresults123.blob.core.windows.net/logs/job.txt?sig=fake-signed-value"


class ProbeStream(httpx.SyncByteStream):
    """Track consumption and cleanup without buffering a real network response."""

    def __init__(self, chunks: list[bytes], *, error: bool = False) -> None:
        self.chunks = chunks
        self.error = error
        self.bytes_yielded = 0
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            self.bytes_yielded += len(chunk)
            yield chunk
        if self.error:
            raise httpx.ReadTimeout("fake-signed-value and raw-log-content")

    def close(self) -> None:
        self.closed = True


def log_transport(
    body: ProbeStream,
    *,
    location: str = DOWNLOAD_URL,
    api_status: int = 302,
    download_status: int = 200,
    api_headers: dict | None = None,
    download_headers: dict | None = None,
    private: bool = False,
) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.method == "GET"
        assert "authorization" not in request.headers
        if request.url.host == "api.github.com":
            if request.url.path == "/repos/example/project":
                return httpx.Response(200, json={"private": private}, headers={"set-cookie": "api_only=fake; Path=/"})
            assert request.url.path == "/repos/example/project/actions/jobs/123/logs"
            return httpx.Response(api_status, headers={"location": location, **(api_headers or {})}, stream=ProbeStream([]))
        assert request.url == httpx.URL(location)
        assert "cookie" not in request.headers
        assert "x-github-api-version" not in request.headers
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(download_status, headers=download_headers, stream=body)

    return httpx.MockTransport(handle), requests


@pytest.mark.parametrize("location", [
    DOWNLOAD_URL,
    "https://pipelines.actions.githubusercontent.com/signedlogcontent?token=fake",
    "https://results-receiver.actions.githubusercontent.com:443/logs?sig=fake",
])
def test_download_returns_only_processed_numbered_evidence(location: str) -> None:
    body = ProbeStream([b"Setup\r\n\x1b[31mTOKEN=fake-secret\x1b[0m\n", b"Tests failed\n"])
    transport, requests = log_transport(body, location=location)

    result = fetch_job_log_excerpt(" Example/Project ", 123, start_line=2, transport=transport)

    assert [(line.source_line, line.text) for line in result.lines] == [(2, "TOKEN=[REDACTED]"), (3, "Tests failed")]
    assert result.total_source_lines == 3
    assert result.redactions_applied and result.truncated
    assert len(requests) == 3
    assert body.closed


def test_private_repository_is_rejected_before_requesting_logs() -> None:
    body = ProbeStream([b"must not be read"])
    transport, requests = log_transport(body, private=True)
    with pytest.raises(GitHubError, match="Only public"):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert len(requests) == 1
    assert body.bytes_yielded == 0


@pytest.mark.parametrize("location", [
    "", "/relative/logs", "//pipelines.actions.githubusercontent.com/logs",
    "http://pipelines.actions.githubusercontent.com/logs",
    "https://127.0.0.1/logs", "https://[::1]/logs", "https://169.254.169.254/logs",
    "https://api.github.com/logs", "https://example.invalid/logs",
    "https://pipelines.actions.githubusercontent.com.example.invalid/logs",
    "https://evilactions.githubusercontent.com/logs",
    "https://productionresults123.blob.core.windows.net.example.invalid/logs",
    "https://pipelines.actions.githubusercontent.com:8080/logs",
    "https://user:fake-password@pipelines.actions.githubusercontent.com/logs",
    "https://@pipelines.actions.githubusercontent.com/logs",
    "https://pipelines.actions.githubusercontent.com/logs#fragment",
    "https://pipelines.actions.githubusercontent.com/\nlogs",
    "https://pipelines.actions.githubusercontent.com\\@example.invalid/logs",
    "https://pipelines.actions.githubusercontent.com:invalid/logs",
    "https://pipelines.actions.githubusercontent.com/" + "a" * 8192,
])
def test_unsupported_redirect_never_reaches_download_host(location: str) -> None:
    body = ProbeStream([b"must not be read"])
    transport, requests = log_transport(body, location=location)
    # HTTPX rejects some malformed Location values before our validator sees them.
    with pytest.raises(GitHubError, match="unsupported log download URL|Could not download GitHub logs"):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert len(requests) == 2
    assert body.bytes_yielded == 0


@pytest.mark.parametrize("status, headers, message", [
    (404, {}, "not found"), (410, {}, "HTTP 410"), (403, {}, "HTTP 403"),
    (403, {"x-ratelimit-remaining": "0"}, "rate limit"),
    (429, {"retry-after": "60"}, "rate limit"),
    (301, {}, "redirected"), (307, {}, "redirected"), (503, {}, "HTTP 503"),
    (200, {}, "HTTP 200"),
])
def test_log_api_errors_are_safe_and_not_retried(status: int, headers: dict, message: str) -> None:
    body = ProbeStream([])
    transport, requests = log_transport(body, api_status=status, api_headers=headers)
    with pytest.raises(GitHubError, match=message):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert len(requests) == 2


@pytest.mark.parametrize("status", [302, 403, 404, 410, 503])
def test_download_errors_and_further_redirects_are_not_followed(status: int) -> None:
    body = ProbeStream([b"raw-log-content"])
    transport, requests = log_transport(body, download_status=status, download_headers={"location": "https://example.invalid"})
    with pytest.raises(GitHubError, match=f"HTTP {status}") as error:
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert "fake-signed-value" not in str(error.value)
    assert "raw-log-content" not in str(error.value)
    assert len(requests) == 3
    assert body.bytes_yielded == 0 and body.closed


@pytest.mark.parametrize("length", [None, "1"])
def test_streaming_cap_is_enforced_even_without_reliable_content_length(length: str | None) -> None:
    body = ProbeStream([b"x" * 8192] * (MAX_INPUT_BYTES // 8192 + 10))
    headers = {} if length is None else {"content-length": length}
    transport, requests = log_transport(body, download_headers=headers)
    with pytest.raises(GitHubError, match="1 MiB"):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert body.bytes_yielded == MAX_INPUT_BYTES + 8192
    assert body.closed and len(requests) == 3


def test_declared_oversize_is_rejected_without_reading_the_body() -> None:
    body = ProbeStream([b"not consumed"])
    transport, _ = log_transport(body, download_headers={"content-length": str(MAX_INPUT_BYTES + 1)})
    with pytest.raises(GitHubError, match="1 MiB"):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert body.bytes_yielded == 0 and body.closed


@pytest.mark.parametrize("headers", [
    {"content-encoding": "gzip"}, {"content-encoding": "br"},
    {"content-length": "-1"}, {"content-length": "invalid"},
    {"content-length": "9" * 5000},
])
def test_unsupported_encoding_or_length_is_rejected_before_reading(headers: dict) -> None:
    body = ProbeStream([b"not consumed"])
    transport, _ = log_transport(body, download_headers=headers)
    with pytest.raises(GitHubError):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert body.bytes_yielded == 0 and body.closed


def test_incomplete_download_is_not_returned_as_evidence() -> None:
    body = ProbeStream([b"partial"])
    transport, _ = log_transport(body, download_headers={"content-length": "100"})
    with pytest.raises(GitHubError, match="incomplete"):
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert body.closed


def test_exact_input_limit_is_accepted_and_output_is_bounded() -> None:
    body = ProbeStream([b"x" * MAX_INPUT_BYTES])
    transport, _ = log_transport(body, download_headers={"content-length": str(MAX_INPUT_BYTES)})
    result = fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert len(result.text.encode()) <= 16_384
    assert result.truncated and body.closed


@pytest.mark.parametrize("body_bytes, start_line", [(b"fake-secret\xff", 1), (b"only one line", 2)])
def test_invalid_log_content_has_safe_errors(body_bytes: bytes, start_line: int) -> None:
    body = ProbeStream([body_bytes])
    transport, _ = log_transport(body)
    with pytest.raises(GitHubError, match="could not be processed") as error:
        fetch_job_log_excerpt("example/project", 123, start_line=start_line, transport=transport)
    assert "fake-secret" not in str(error.value)
    assert body.closed


def test_midstream_timeout_discards_partial_content_and_signed_url() -> None:
    body = ProbeStream([b"raw-log-content"], error=True)
    transport, requests = log_transport(body)
    with pytest.raises(GitHubError, match="Could not download") as error:
        fetch_job_log_excerpt("example/project", 123, transport=transport)
    assert "fake-signed-value" not in str(error.value)
    assert "raw-log-content" not in str(error.value)
    assert body.closed and len(requests) == 3


@pytest.mark.parametrize("repository, job_id, start_line", [
    ("https://example.invalid/project", 123, 1), ("example/project", 0, 1),
    ("example/project", 2**63, 1), ("example/project", True, 1),
    ("example/project", 123, 0), ("example/project", 123, True),
])
def test_invalid_inputs_do_not_make_http_requests(repository: str, job_id: int, start_line: int) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        pytest.fail("Invalid inputs must be rejected before HTTP requests")

    with pytest.raises(ValueError):
        fetch_job_log_excerpt(repository, job_id, start_line=start_line, transport=httpx.MockTransport(handle))
