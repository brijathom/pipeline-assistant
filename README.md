# Pipeline Assistant

An AI-assisted CI/CD reliability and investigation platform, starting with GitHub Actions.

## Current scope

The backend exposes a health endpoint and interactive API documentation, with
SQLAlchemy models and an Alembic migration for pipeline runs in PostgreSQL.
GitHub integration, the runs API, the frontend, and AI investigations are planned.

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
and roll back uncommitted work. No endpoint uses the database yet, so `/health`
still works without database configuration.

The database is bound only to localhost, on port 5432 by default. If that port is
already in use, stop the conflicting service or configure a different host port
in both `.env` files. Data survives container restarts in
a named Docker volume. From the repository root, `docker compose stop db` stops
PostgreSQL without removing its data. Changing `.env` credentials does not change
credentials inside an already initialized database volume.

## Test

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

The API test uses an in-process HTTP client; no running server or credentials are required.

## Dependencies

`backend/pyproject.toml` declares application dependencies and the optional `dev`
dependencies. Direct dependencies are pinned to the versions used for this initial
skeleton; transitive dependencies are not yet locked.

## License

[MIT](LICENSE).
