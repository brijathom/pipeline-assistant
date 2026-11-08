# Pipeline Assistant

An AI-assisted CI/CD reliability and investigation platform, starting with GitHub Actions.

## Current scope

The backend exposes a health endpoint and interactive API documentation, with
SQLAlchemy models and an Alembic migration for pipeline runs in PostgreSQL.
Fictional demo data, read-only ingestion of public GitHub Actions runs, runs and
metrics APIs, job/step evidence, bounded log imports, and a React dashboard are
included. Curated fictional log excerpts and stored rule-based investigation
reports are available in run details. An optional Google ADK command generates
private AI drafts for review; the public report generator remains rule-based.

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

Migration `0003` adds `pipeline_job_logs`, with one current processed excerpt per
job. The primary key is also a cascading foreign key to its job. PostgreSQL checks
the 16 KiB text limit, 100-line limit, source range, and evidence source kind.
Stored fields include the first source line, line count, total source lines,
truncation/redaction flags, curation flag, and update timestamp. There is no raw
log or signed-download-URL column. Removing a local job or run removes its excerpt.

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
The failed run, GitHub ID `1002`, also receives a fictional job, two steps, and a
curated processed excerpt. Filter the dashboard to `demo/pipeline-assistant`, open
the failed run, and select **View log excerpt** to explore it.

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
stored evidence independently. Each job has an on-demand log excerpt viewer.

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

**View log excerpt** fetches `GET /api/runs/{run_id}/jobs/{job_id}/log` using local
database IDs. The endpoint checks the job belongs to that run attempt and returns
HTTP 404 for a missing or mismatched job. For an existing job, both missing and
uncurated excerpts return JSON `null`, without exposing their contents or metadata.
Curated excerpts include numbered lines, source line count, truncation/redaction
flags, evidence source, and update time. An empty curated log is an object with an
empty line array, distinct from an unavailable excerpt. Responses use `no-store`.

The viewer renders plain text with source line numbers, marks fictional evidence
and partial excerpts, and wraps long lines on mobile. Loading and retry are local
to the selected job. **Refresh log** reloads stored evidence without contacting
GitHub; a transient failure retains cached evidence with a warning. A successful
`null` response or HTTP 404 clears previously displayed evidence. It has its own
query cache keyed by run and job; **Refresh details** refreshes run/jobs metadata.

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

### Log processing foundation

`backend/app/log_processing.py` provides a pure function, `process_log`, for
preparing evidence before persistence or display. It accepts complete UTF-8 log
bytes up to 1 MiB, normalizes newlines, removes terminal controls, and masks
common credential patterns. These include GitHub token prefixes, JWTs, private
key blocks, authorization values, URL credentials, and sensitive assignments.
Sensitive assignments conservatively mask the remainder of their line.

Redaction runs on the complete bounded input before selecting or shortening an
excerpt, preserving context such as a private-key header outside the selected
lines. Oversized or invalid UTF-8 input is rejected with errors that omit log
contents. The downloader also enforces the input cap while receiving bytes.

Excerpts contain at most 100 lines and 16 KiB of joined UTF-8 text, including
newline separators and truncation markers. Each line retains its one-based
source line number. Metadata reports the source line count, whether content was
omitted, and whether redaction patterns matched. Callers can reduce the limits.

Pattern matching cannot identify every secret, including arbitrary unlabelled
values. It is not proof that output is safe to publish; use curated evidence for
the public demo or an AI provider. Processing tests use synthetic logs only.

### Bounded job-log retrieval

`fetch_job_log_excerpt` in `backend/app/github.py` retrieves one public GitHub
job's logs and returns a processed `LogExcerpt`. Callers provide a validated
repository and GitHub job ID, with an optional starting source line. The function
confirms the repository is public and makes unauthenticated GET requests only.

GitHub's [job-log endpoint](https://docs.github.com/en/rest/actions/workflow-jobs#download-job-logs-for-a-workflow-run)
returns a 302 with a temporary download URL. Only an absolute HTTPS URL on port
443 under `*.actions.githubusercontent.com` or an Azure Blob storage account
under `*.blob.core.windows.net` is accepted. These hosts reflect GitHub's
[documented Actions infrastructure](https://docs.github.com/en/actions/reference/runners/github-hosted-runners#communication-requirements-for-github-hosted-runners).
Azure Blob storage is shared infrastructure; the URL must come directly from the
trusted GitHub API response, never from a visitor. Credentials in the URL's
authority, fragments, and further redirects are rejected. A separate HTTP client
downloads the content without forwarding API headers or cookies.

The downloader requests uncompressed content and enforces the 1 MiB limit while
streaming, even when `Content-Length` is absent or misleading. It rejects encoded,
oversized, or incomplete responses rather than processing partial evidence.
Connections time out after five seconds; individual reads time out after ten
seconds. Requests are not automatically retried. Download errors omit response
bodies and signed URLs, which can contain temporary access credentials.

Raw bytes stay in memory and pass through `process_log` before anything is
returned to the caller. HTTP tests use mocked streams and synthetic content;
they do not contact GitHub. Dashboard refresh does not download logs.

### Store a processed job-log excerpt

After migrating and importing jobs, choose a local job ID from
`GET /api/runs/{id}/jobs`. From `backend`, replace `123` with that local job ID:

```powershell
.\.venv\Scripts\python.exe -m app.import_logs --job-id 123 --start-line 1
```

The command derives the repository and GitHub job ID from the stored job/run,
rejects missing and fictional sources before network access, and closes the read
session before downloading. Once processing succeeds, a short transaction locks
and rechecks both source identities, then atomically replaces that job's excerpt.
A failed download or write preserves previous evidence. Raw bytes and excerpt
contents are not printed; only processed text and metadata are persisted.

Every GitHub import sets `is_curated` to false, including replacement of a
previously curated excerpt. Redaction alone never publishes new content. Since
the API currently has no authentication, it serves only curated evidence; the
fictional seeder is currently the only publication path provided by the app.
There is no real-log curation command or review UI yet. Imported GitHub excerpts
remain hidden from the public API, and there is no visitor-triggered import
endpoint. A curation workflow will be added with access controls.

## Investigation foundation

The failed demo run (`demo/pipeline-assistant`, GitHub run `1002`) includes a
stored investigation. Open its details and scroll to **Investigation** to see
observations, suggested next steps, exact log quotes, source line numbers, and
explicit limitations. Public reports are clearly labelled **Rule-based evidence
summary** and do not call an AI model. The separate optional ADK command below
creates private drafts. Recommendations are advisory and never modify or rerun
a workflow.

From `backend`, generate or refresh a report for one stored run, replacing `123`
with its local database ID:

```powershell
.\.venv\Scripts\python.exe -m app.investigate_run --run-id 123
```

Generation uses stored evidence only, with no network calls. A failed job is one
with a `failure`/`timed_out` conclusion or a stored step with either conclusion.
The snapshot includes at most ten such jobs with curated excerpts, ordered by
GitHub job ID and local ID. Other jobs contribute to a count of omitted evidence;
uncurated excerpts never enter the snapshot. Names are bounded and each excerpt
retains the existing 100-line/16 KiB limits. Diagnostic quotes are limited to
4,096 characters. Missing or insufficient evidence yields explicit limitations
instead of an invented cause. If no failures are recorded, the report says so.

Pydantic validates the versioned report structure. Before persistence, every
citation must match a job, source line, and exact quote in the evidence snapshot.
The deterministic generator recognizes HTTP status assertions, timeout messages,
and generic failure diagnostics. It reports observations, not a proven root
cause; citation validation alone does not establish that a future model's reasoning
is correct. The structured contract can later be used by an AI generator.

Migration `0004` creates `pipeline_investigations`: one current report per run,
with cascading cleanup when its run is deleted. PostgreSQL JSONB stores the
variable report and evidence documents, with object-type and 512 KiB checks;
the run foreign key and generation metadata remain relational columns. A SHA-256
fingerprint binds the report to the selected evidence and source identity.
Generation runs outside the read transaction, then a short write transaction
locks the parent, rechecks the fingerprint, and saves atomically. Unchanged
evidence is idempotent; generation or write failures preserve the previous report.

`GET /api/runs/{id}/investigation` returns `ready`, `not_generated`, or `stale`.
A missing run returns HTTP 404. Before serving a stored report, the API compares
its fingerprint with current curated evidence. Changed or withdrawn evidence
returns `stale` without the previous quotes, report, or snapshot. Internal evidence
snapshots are never returned. Responses use `no-store`; the UI hides report
content on a refresh error because freshness could not be verified. **Refresh
investigation** reloads stored data and does not generate a report. No public
generation endpoint is exposed.

The next major work remains live model evaluation, authenticated review/approval
workflows, Pub/Sub processing and SSE progress, timing metrics, and containerized
public deployment on Google Cloud Run with production checks.

### Optional Google ADK drafts

The `ai` dependency group pins Google ADK and Google GenAI separately from the
core API. Install it from `backend` in PowerShell:

```powershell
$env:PYYAML_FORCE_LIBYAML = "0"
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai]"
```

The environment setting selects PyYAML's pure-Python build when Python 3.15 has
no matching wheel, avoiding a requirement for Microsoft C++ Build Tools. It
affects only that terminal. On Linux/macOS, use
`PYYAML_FORCE_LIBYAML=0 .venv/bin/python -m pip install -e ".[dev,ai]"`.

Add a Gemini API key to the ignored `backend/.env` locally; never paste it into
code, Git, test fixtures, or chat. `GEMINI_API_KEY` is optional for the normal
application. `AI_MODEL` defaults to `gemini-3.8-flash` and accepts Gemini model
names, not URLs. This first provider uses the Gemini Developer API with explicit
key authentication; Vertex AI/Google Cloud identity integration is future work.
See Google's [API-key setup](https://ai.google.dev/gemini-api/docs/api-key) and
[model documentation](https://aistudio.google.com/docs/models/gemini-3.8-flash).

With a key configured, this explicit command sends curated evidence to Gemini
and can incur model charges. Use the **local database ID** of the failed demo
run from `/api/runs?repository=demo/pipeline-assistant`, not GitHub run number
`1002`. From `backend`, replace `123` with that local ID:

```powershell
.\.venv\Scripts\python.exe -m app.draft_investigation --run-id 123
```

`app/ai_investigation.py` creates a single `LlmAgent` with a fresh in-memory ADK
session, no tools, and no subagents. Log text and names are treated as untrusted
data in the prompt. Inputs are capped at 64 KiB, output at 8,192 tokens and
128 KiB of final text, and generation at 60 seconds. The Gemini HTTP client has
a 45-second timeout, fixed Google endpoint, and one attempt; ADK allows one
model call. There are no repair calls or automatic retries. These bounds limit
work but are not a currency-based spending cap.

ADK requests structured output. A small provider schema translates the numeric
report-version constraint for the Gemini SDK; the result is then validated
against our canonical Pydantic schema and exact source-line citations. Citation
checks establish that quotes exist, not that every inference is correct.
Model-error callbacks replace provider exceptions before ADK can emit their raw
tracebacks. The application does not print prompts, reports, or API keys.

The command releases the database connection before generation and rechecks the
evidence fingerprint afterward. If evidence changed, no draft is written. A
successful run creates a uniquely named JSON file under ignored `backend/.drafts/`,
containing `pending_review` status, model/prompt metadata, evidence fingerprint,
snapshot, and report. Existing files and public database reports are not replaced.
Review the draft in your editor for factual claims, citations, sensitive values,
and recommendations. Approval and publication are not implemented in this slice;
the API and UI never read these draft files.

Tests exercise the real ADK runner with a fake model, plus the real Gemini client
with mocked HTTP. They cover output validation, invented citations, provider
failure, timeout cancellation, prompt/response caps, and preservation of public
reports. No credentials or paid calls are needed for these tests. A live provider
smoke test and evaluation of model quality remain outstanding.

## Test

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

To run just the synthetic log-processing tests without a database:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_log_processing.py -q
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
  backend with development and optional AI dependencies, checks dependency consistency, and runs
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
