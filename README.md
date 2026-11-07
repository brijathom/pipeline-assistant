# Pipeline Assistant

An AI-assisted CI/CD reliability and investigation platform, starting with GitHub Actions.

## Current scope

The backend exposes a health endpoint and interactive API documentation, with
SQLAlchemy models and an Alembic migration for pipeline runs in PostgreSQL.
Fictional demo data, a read-only runs API, and a React dashboard are included.
GitHub integration and AI investigations are planned.

## Backend setup

Requires Python 3.12. From the repository root in PowerShell:

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

If `.venv` already exists, skip the environment creation command.
Calling its Python executable directly avoids requiring shell activation.

On Linux or macOS, create the environment with `python3.12 -m venv .venv`
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
refresh preserves previously loaded rows and shows a warning. Times are in UTC.

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
states. The live check expects the fictional demo seed data to be present.

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
the result limit, response serialization, invalid limits, and seed idempotency.

## Dependencies

`backend/pyproject.toml` declares application dependencies and the optional `dev`
dependencies. Direct dependencies are pinned to the versions used for this initial
skeleton; transitive dependencies are not yet locked.

## License

[MIT](LICENSE).
