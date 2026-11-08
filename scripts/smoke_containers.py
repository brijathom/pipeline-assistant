"""Build and test an isolated local stack; remove only its temporary Compose project."""
import argparse
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PROJECT_PATTERN = re.compile(r"pipeline-assistant-smoke-[0-9a-f]{32}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="Reuse previously built local API/web images")
    args = parser.parse_args()
    project = f"pipeline-assistant-smoke-{uuid4().hex}"
    if PROJECT_PATTERN.fullmatch(project) is None:
        raise RuntimeError("Refusing an unexpected smoke project name.")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_urlsafe(32)  # Ephemeral fake operator credential, never logged or saved.
    environment = {**os.environ, "DEMO_PORT": str(port), "DEMO_REVIEWER_ACCESS_TOKEN": token}
    command = ["docker", "compose", "--project-name", project, "--file", str(ROOT / "compose.app.yaml")]
    opener = build_opener(ProxyHandler({}))
    base = f"http://127.0.0.1:{port}"
    started = False
    success = False

    def compose(*arguments: str, timeout: int = 180, capture: bool = False):
        print(f"Docker stage: {arguments[0]}", flush=True)
        result = subprocess.run([*command, *arguments], cwd=ROOT, env=environment, timeout=timeout,
            check=True, capture_output=capture, text=True)
        return result.stdout.strip() if capture else None

    def request(path: str, *, authenticated: bool = False, method: str = "GET", body: bytes | None = None):
        headers = {"Authorization": f"Bearer {token}"} if authenticated else {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        return opener.open(Request(base + path, headers=headers, method=method, data=body), timeout=5)

    def read_json(path: str, **kwargs):
        with request(path, **kwargs) as response:
            assert response.headers.get_content_type() == "application/json", "API returned a non-JSON response."
            return json.load(response)

    def expect_status(path: str, status: int, **kwargs):
        try:
            with request(path, **kwargs) as response:
                actual, headers = response.status, response.headers
        except HTTPError as error:
            actual, headers = error.code, error.headers
            error.close()
        assert actual == status, f"Unexpected HTTP status for {path}: {actual}."
        return headers

    def wait_for_api():
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                return read_json("/api/runs?limit=20")
            except (HTTPError, URLError, TimeoutError):
                time.sleep(0.5)
        raise RuntimeError("The API did not become available through the web proxy.")

    try:
        print("Checking Docker engine (15-second timeout).", flush=True)
        subprocess.run(["docker", "version", "--format", "{{.Server.Version}}"], env=environment, timeout=15, check=True)
        compose("config", "--quiet", timeout=15)
        if not args.skip_build:
            compose("build", timeout=900)
        started = True  # Ensure a partially successful startup is also cleaned up.
        compose("up", "--detach", "--wait", timeout=180)
        compose("run", "--rm", "--no-deps", "api", "python", "-m", "app.seed")
        print("Checking seeded API responses through the frontend proxy.", flush=True)
        runs = wait_for_api()
        assert len(runs) == 3 and all(run["repository"] == "demo/pipeline-assistant" for run in runs)
        failed = next(run for run in runs if run["github_run_id"] == 1002)
        assert read_json("/api/metrics")["total_runs"] == 3
        performance = read_json("/api/metrics/performance?days=7")
        assert len(performance["daily"]) == 7
        report = read_json(f"/api/runs/{failed['id']}/investigation")
        assert report["status"] == "ready" and report["generation_method"] == "rules-v1"
        with request("/") as response:
            html = response.read().decode()
            assert '<div id="root"></div>' in html and "no-cache" in response.headers.get("Cache-Control", "")
            assert response.headers.get("Content-Security-Policy")
        asset = re.search(r'src="(/assets/[^\"]+\.js)"', html)
        assert asset, "The frontend bundle was not referenced in index.html."
        with request(asset.group(1)) as response:
            assert "immutable" in response.headers.get("Cache-Control", "")
            assert response.headers.get_content_type() in {"application/javascript", "text/javascript"}
        expect_status("/healthz", 200)
        for path in ("/.env", "/.git/config", "/assets/missing.js", "/api", "/api/does-not-exist"):
            expect_status(path, 404)
        print("Checking operator authentication and SSE delivery.", flush=True)
        assert expect_status("/api/reviewer/tasks", 401)["Cache-Control"] == "no-store"
        assert read_json("/api/reviewer/tasks", authenticated=True) == []
        # Even an authenticated operator cannot queue a paid model call in this image/stack.
        expect_status("/api/reviewer/tasks", 503, authenticated=True, method="POST",
            body=json.dumps({"request_id": str(uuid4()), "run_id": failed["id"]}).encode())
        began = time.monotonic()
        with request("/api/reviewer/tasks/events", authenticated=True) as response:
            assert response.headers.get_content_type() == "text/event-stream"
            assert response.headers.get("Cache-Control") == "no-store"
            assert response.readline().startswith(b"id: ")
            assert response.readline().strip() == b"event: tasks"
            assert response.readline().strip() == b"data: []"
        assert time.monotonic() - began < 5, "The proxy buffered the first SSE frame."
        print("HTTP checks passed: SPA/assets, real API/database reads, operator auth and unbuffered SSE.", flush=True)

        uid = compose("exec", "-T", "api", "python", "-c", "import os; print(os.getuid())", capture=True)
        assert uid == "10001"
        assert compose("exec", "-T", "web", "id", "-u", capture=True) == "101"
        compose("exec", "-T", "api", "python", "-c",
            "import os; from pathlib import Path; assert os.getuid()!=0; "
            "assert not Path('/srv/app/.env').exists(); "
            "assert not Path('/srv/app/.drafts').exists(); "
            "assert not Path('/srv/app/.evals').exists(); "
            "assert not os.getenv('GEMINI_API_KEY'); print('Runtime isolation checks passed.')")
        original_ids = {run["id"] for run in runs}
        compose("up", "--detach", "--no-deps", "--force-recreate", "api")
        assert {run["id"] for run in wait_for_api()} == original_ids
        print("API replacement preserved demo data and the frontend proxy recovered.", flush=True)
        success = True
    except (AssertionError, RuntimeError, subprocess.SubprocessError, OSError, ValueError) as error:
        # Do not echo environment, HTTP bodies, tokens or container logs automatically.
        print(f"Container smoke failed ({type(error).__name__}). Inspect the failing stage above.", file=sys.stderr)
    finally:
        if started:
            # The generated namespace is the only project this script ever creates/deletes.
            try:
                compose("down", "--volumes", "--remove-orphans", timeout=90)
            except (subprocess.SubprocessError, OSError):
                print(f"Temporary stack cleanup failed; its project name is {project}.", file=sys.stderr)
                success = False
    if success:
        print("Container smoke passed. Only its temporary stack/volume were removed; local images were retained.")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
