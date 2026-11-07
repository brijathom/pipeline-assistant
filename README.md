# Pipeline Assistant

An AI-assisted CI/CD reliability and investigation platform, starting with GitHub Actions.

## Current scope

The backend exposes a health endpoint and interactive API documentation, with
SQLAlchemy models and an Alembic migration for pipeline runs in PostgreSQL.
Fictional demo data, read-only ingestion of public GitHub Actions runs, runs and
metrics APIs, and a React dashboard are included. AI investigations are planned.

## Backend setup

Requires Python 3.15 (validated with 3.15.2). `backend/.python-version` records the
tested patch version for compatible version managers. From the repository root
in PowerShell:

```powershell
cd backend
py -3.15 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If `.venv` already uses Python 3.15, skip the environment creation command.
After upgrading from an older Python minor version, recreate the virtual
environment with Python 3.15 and reinstall dependencies; do not edit `pyvenv.cfg`.
Calling its Python executable directly avoids requiring shell activation.

On Linux or macOS, create the environment with `python3.15 -m venv .venv`
and use `.venv/bin/python` instead of `.\.venv\Scripts\python.exe` below.

## Run locally

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

- Health: <http://127.0.0.1:8000/health> returns `{"status":"ok"}`.
- Interactive API documentation: <http://127.0.0.1:8000/docs>.

The health endpoint checks that the application can respond. It does not check
external dependencies. `--reload` is for local development.

## Frontend

Requires Node.js 24 and npm. Check `node --version` before installing; the
frontend enforces this requirement. `.node-version` records the Node major
version for compatible version managers.

With the local database migrated and seeded, start FastAPI using the command
above. In a second terminal, from the repository root:

```powershell
cd frontend
npm ci
npm run dev
```

Open <http://127.0.0.1:5173>. The dashboard displays up to 20 recent runs, including
loading, empty, and retryable error states. Refresh reloads the data; a failed
refresh preserves previously loaded rows and shows a warning. Four overview cards
count all stored attempts for the selection, independently of the recent-run
limit. The repository filter applies to both cards and table. Leave it empty for
all repositories, or enter a name such as `actions/setup-node`. Refresh reloads
stored data; it does not fetch new runs from GitHub. Times are in UTC.

The browser requests relative `/api` URLs. Vite proxies these requests to
`http://127.0.0.1:8000` during development, so no backend CORS changes are needed.
TanStack Query owns caching and request state, with a 30-second stale time and
one automatic retry. The TypeScript response interface documents the API contract;
it does not perform runtime JSON validation.

Vite's proxy is a development feature. A production host must route `/api` to
FastAPI separately; `npm run preview` only serves the frontend build.

To check types and create the production bundle, from `frontend`:

```powershell
npm run build
```

`npm run typecheck` runs only the TypeScript check. Commit `package-lock.json`
with dependency changes; `npm ci` installs the locked dependency tree.

### Project-local Node on Windows

A portable Node runtime downloaded during development is kept under the ignored
`.tools` directory. If present, select it for the current PowerShell terminal
from the repository root before running npm:

```powershell
$nodeRuntime = Get-ChildItem .tools -Directory -Filter 'node-v24.*-win-x64' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $nodeRuntime) { throw 'Install Node.js 24 or provide a portable runtime in .tools.' }
$env:Path = $nodeRuntime.FullName + ';' + $env:Path
node --version
```

This changes only the current terminal's PATH. A system installation of Node.js 24
also works and does not require this step.

### Browser tests

With the development database running, migrated, and seeded, run from `frontend`:

```powershell
npm run test:e2e
```

Playwright starts FastAPI and Vite when needed and stops the servers it started
afterward. Existing local servers are reused. Tests include a real, read-only
browser-to-API check and intercepted responses for loading, empty, and error/retry
states. The live check expects at least one seeded or imported run to be present.

On Windows, a restricted automation sandbox can block Playwright from stopping
the server processes it started, leaving the command waiting after tests pass.
If this happens, run the tests from a normal VS Code terminal. Automated test
runs need permission to terminate their own child processes; increasing the
server startup timeout will not fix blocked cleanup.

On Windows the tests use installed Microsoft Edge. Elsewhere, install Playwright's
Chromium with `npx playwright install chromium`. Set `PLAYWRIGHT_CHANNEL` to use
another installed browser channel, such as `chrome`. Browser results, screenshots,
and traces are ignored by Git.

## Local database

Requires Docker Desktop with the Linux container engine running and Docker Compose v2.
From the repository root:

```powershell
Copy-Item .env.example .env
Copy-Item backend/.env.example backend/.env
docker compose up -d --wait db
```

Copy each environment template only on first setup, to avoid replacing your local
settings. Both `.env` files are ignored by Git. The example credentials are for
local development only. If you change them or the port in the root `.env`, update
the connection URL in `backend/.env` to match. URL-encode special characters in
the username or password. Production should inject `DATABASE_URL` through its
environment or secret manager; environment variables override the dotenv file.

After installing the backend dependencies, run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic check
```

Alembic applies versioned migrations explicitly; starting the API does not create
or modify tables. `alembic check` detects differences between the models and
the migrated schema. To create a future migration, use
`python -m alembic revision --autogenerate -m "Describe schema change"` with the
virtual environment's Python, review the generated migration, then apply it.

The first migration creates `pipeline_runs`. Each row represents a GitHub run
attempt, unique by repository, GitHub run ID, and attempt number. Status and
conclusion are separate; unfinished runs may have no conclusion or completion
timestamp. Source timestamps use PostgreSQL `timestamp with time zone`;
`ingested_at` records when the row reached our database.

Migration `0002` adds `pipeline_jobs` and `pipeline_job_steps`. Each job references
one local run attempt and is unique by `(pipeline_run_id, github_job_id)`. Each
step references one job and is unique by `(pipeline_job_id, step_number)`. Job
IDs and step numbers must be positive; step numbers can have gaps, and names
can repeat. Names use text columns to accommodate long workflow labels.

Jobs and steps store status, nullable conclusion, nullable start/completion
timestamps, and ingestion time. Unknown source timestamps remain null. Foreign
keys prevent orphaned evidence; deleting a local run cascades to its jobs and
steps, while deleting a job removes its steps. These rules apply to local stored
data. The job importer below populates this evidence, and the run-details panel
displays the stored jobs and steps for the selected attempt.

SQLAlchemy uses synchronous sessions, intended to be supplied per request via
`get_session`. Write operations must commit explicitly. Sessions close after use
and roll back uncommitted work. `/health` does not access the database and still
works without database configuration.

The database is bound only to localhost, on port 5432 by default. If that port is
already in use, stop the conflicting service or configure a different host port
in both `.env` files. Data survives container restarts in
a named Docker volume. From the repository root, `docker compose stop db` stops
PostgreSQL without removing its data. Changing `.env` credentials does not change
credentials inside an already initialized database volume.

## Demo runs and runs API

After migrating the local development database, run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.seed
```

This explicitly inserts three fictional runs (successful, failed, and in progress)
for `demo/pipeline-assistant`. It never calls GitHub. Running it again preserves
existing rows and inserts no duplicates. Seed data is not inserted at API startup.

`GET /api/runs` returns a JSON array
of runs ordered by creation time descending, then ID descending. `limit` defaults
to 20 and accepts values from 1 to 100. Try `/api/runs?limit=2` or use `/docs`.
Responses include source timestamps and results, but omit internal ingestion metadata.

### Run details

Click a workflow name in the table to open its details panel. The panel fetches
`GET /api/runs/{id}` using the local database ID from the list response, not the
GitHub run ID. Each rerun attempt has its own local ID and details cache entry.
The endpoint returns the same public fields as a list entry, HTTP 404 when the
stored attempt is missing, or HTTP 422 for invalid or out-of-range IDs.

Details show the repository, GitHub run ID, attempt, status, conclusion, and
available source timestamps in UTC. Missing timestamps remain unavailable;
completion times and durations are not inferred. This is a stored snapshot;
opening or refreshing details does not contact GitHub. The jobs section loads
stored evidence independently; logs are not included yet.

The panel has independent loading, retry, and not-found states. Failed refreshes
retain cached details with a warning; a 404 hides them and is not automatically
retried. Close the panel using its close button or Escape. The native dialog
contains keyboard focus and returns it to the workflow button when closed.

`GET /api/runs/{id}/jobs` returns a JSON array of stored jobs with nested steps.
Jobs are ordered by GitHub job ID and steps by source step number, including
numbering gaps. The response contains names, status, conclusion, source
timestamps, local IDs, and GitHub job IDs; internal parent keys and ingestion
metadata are omitted. A missing run returns HTTP 404; an existing run with no
stored jobs returns an empty array. Two batch evidence queries keep database
query count constant as the number of jobs grows, after the parent lookup.

The panel highlights failed or timed-out steps and automatically expands jobs
with a failure or failed steps. Successful jobs without failed steps start
collapsed. Jobs with no steps and unknown timestamps have explicit empty states.
"No stored jobs yet" does not distinguish an unimported attempt from an imported
empty result. The jobs section has independent loading and retry states and
retains cached evidence with a warning if a refresh fails. **Refresh details**
reloads both run metadata and jobs; it does not trigger an import.

### Reliability metrics

`GET /api/metrics` returns `repository` (null for all repositories), `total_runs`,
`successful_runs`, `failed_runs`, and `in_progress_runs`. Both `/api/metrics` and
`/api/runs` accept an optional `repository=owner/repository` query parameter.
Names are trimmed and normalized to lowercase; malformed names return HTTP 422.
Unknown repositories return zero counts and an empty run list.

One PostgreSQL aggregate query counts all stored attempts for the selection:
successful means status `completed` and conclusion `success`; failed means
`completed` with `failure` or `timed_out`; in progress means status `in_progress`.
Queued, waiting, cancelled, skipped, neutral, and unknown outcomes contribute to
the total without being treated as successes or failures. The four values are
not an exhaustive partition of the total.

Rerun attempts count separately when imported. These are observed stored
attempts, not full GitHub history or a live status feed. No date window or
duration is implied. Metrics have independent loading and retry states; failed
refreshes retain cached counts with a warning, just as the run list retains rows.

## Import public GitHub Actions runs

Set one repository in `backend/.env`:

```dotenv
GITHUB_REPOSITORY=actions/setup-node
```

With the development database running and migrated, run from `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.import_runs
```

The command validates the repository name, confirms the repository is public,
and fetches the latest page of runs. It defaults to 20 runs; use `--limit 5` to
fetch fewer, up to a maximum of 100. Refresh the dashboard to see the imported
rows alongside any existing demo data. GitHub reads finish and the full payload
is validated before the database transaction starts.

Each repository/run ID/attempt combination is inserted or refreshed atomically,
so repeating an import updates changing statuses without creating duplicates.
Later attempts of the same run are separate records when observed. The importer
fetches only the latest attempt returned for each run; this is a bounded snapshot,
not a full historical backfill or scheduled synchronization.

Only unauthenticated GET requests to `api.github.com` are made. No GitHub token is
needed. The importer does not follow redirects or pagination links, and reports
timeouts, rate limits, missing repositories, and invalid responses with a nonzero
exit code. Public unauthenticated requests have a lower rate limit; wait before
retrying when GitHub rejects a request. No import endpoint is exposed to visitors.

`created_at` and `started_at` use GitHub's `created_at` and `run_started_at` fields.
The run-list response has no completion timestamp, so new imports leave
`completed_at` unknown. `updated_at` is not used as a substitute for completion
time. Accurate duration metrics will require job timing data in a later step.

GitHub HTTP tests use a mock transport and fictional fixtures; CI never needs
GitHub credentials or network access to test this integration. PostgreSQL tests
cover repeated imports, status updates, and separate rerun attempts.

### Import jobs and steps for one attempt

After applying migrations, choose a non-demo run from `GET /api/runs` or `/docs`.
Use its local `id`, not its `github_run_id`. From `backend`, replacing `123` with
that local ID:

```powershell
.\.venv\Scripts\python.exe -m app.import_jobs --run-id 123
```

The importer derives the repository, GitHub run ID, and attempt from that stored
row, so changing `GITHUB_REPOSITORY` does not redirect an existing run's evidence.
Missing local IDs and fictional `demo/` runs are rejected before contacting
GitHub. The repository must still be public; only unauthenticated GET requests
are made. No import endpoint is exposed to dashboard visitors.

The command uses GitHub's [jobs-for-an-attempt endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#list-jobs-for-a-workflow-run-attempt).
It fetches up to five pages of 100 jobs (500 jobs maximum), constructing each
page request against `api.github.com` rather than following pagination URLs.
It requires complete pages and stable totals, validates every job/step, and
rejects duplicate job IDs, duplicate step numbers, and mismatched run identities.
A failed page or an attempt exceeding the limit leaves existing evidence intact.

A short read session identifies the run and closes before network requests.
Once fetching finishes, a new transaction locks and rechecks the parent identity,
then atomically upserts jobs and steps. Repeating the command preserves local
record IDs while refreshing names, statuses, conclusions, and source timestamps.
Jobs or steps omitted from a later response are retained as observed evidence;
this command does not delete records to mirror a shrinking response. Reported
counts are records processed in this import, not newly inserted records.

Job and step timestamps remain source values. Workflow completion times are not
inferred from job completion times. The importer does not retrieve logs. Open
the matching run in the dashboard to explore its stored jobs and steps.

## Test

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Without `TEST_DATABASE_URL`, PostgreSQL integration tests are skipped; the health
and configuration tests still run. API tests use an in-process HTTP client, so
no running API server is needed.

To run integration tests, start the dedicated disposable test database from the
repository root:

```powershell
docker compose --profile test up -d --wait db-test
```

Then, from `backend`:

```powershell
$env:TEST_DATABASE_URL = "postgresql+psycopg://pipeline_test:local-test-only@127.0.0.1:5433/pipeline_assistant_test"
.\.venv\Scripts\python.exe -m pytest
```

The fixture rejects URLs that do not match this dedicated local test database,
applies Alembic migrations, and rolls back each test's data, including explicit
session commits. The test database uses temporary storage and is separate from
the persistent development database. Use `docker compose --profile test stop db-test`
from the repository root when finished; its data is disposable.

Tests cover empty results, deterministic ordering,
the result limit, response serialization, invalid limits, seed idempotency, and
repository-scoped metrics across all stored attempts, including non-success
outcomes and repeated attempts.

Job/step tests exercise parent-scoped uniqueness, foreign keys, cascading
cleanup, nullable results, and timezone-aware timestamp round trips in PostgreSQL.
A migration test upgrades, downgrades, and upgrades again in an isolated,
transaction-local schema inside the guarded test database. It verifies that
existing runs survive the new migration and its rollback; it never downgrades
the development database.

## Continuous integration

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on pushes and pull
requests. Two independent Ubuntu 24.04 jobs check the application:

- **Backend tests:** reads Python from `backend/.python-version`, installs the
  backend with development dependencies, checks dependency consistency, and runs
  Pytest. A disposable PostgreSQL 17 service exposes port 5433 with the dedicated
  test database settings. The test fixture applies migrations and rolls back test
  data. `TEST_DATABASE_URL` is set explicitly so integration tests run in CI.
- **Frontend build:** reads Node from `frontend/.node-version`, installs the
  dependency tree with `npm ci`, then runs the TypeScript check and Vite build.

Both jobs use read-only repository permissions. Database credentials are only
for the disposable CI service; no repository secrets or local `.env` files are
required. GitHub Actions are pinned to commit SHAs with their release versions
in comments. Dependency download caches use the backend manifest and frontend
lockfile as inputs. New runs cancel older runs for the same Git ref, and each
job has a time limit.

After pushing, open the repository's **Actions** tab, select **CI**, and inspect
the two jobs. A failed test or build fails its job. Branch protection can later
require these checks before merging. Browser test instructions are above; the
current CI checks cover backend tests and the frontend build.

## Dependencies

`backend/pyproject.toml` declares application dependencies and the optional `dev`
dependencies. Direct dependencies are pinned to the versions used for this initial
skeleton; transitive dependencies are not yet locked.

## License

[MIT](LICENSE).
