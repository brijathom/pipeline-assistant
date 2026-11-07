# Pipeline Assistant

An AI-assisted CI/CD reliability and investigation platform, starting with GitHub Actions.

## Current scope

The backend skeleton exposes a health endpoint and interactive API documentation.
GitHub integration, persistence, the frontend, and AI investigations are planned.

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
