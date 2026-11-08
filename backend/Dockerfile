# syntax=docker/dockerfile:1
ARG PYTHON_IMAGE=python:3.15.2-slim-trixie@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
FROM ${PYTHON_IMAGE} AS build
WORKDIR /build
RUN python -m venv /opt/venv
COPY pyproject.toml ./
COPY app/ ./app/
RUN /opt/venv/bin/python -m pip install --no-cache-dir . \
    && /opt/venv/bin/python -m pip check

FROM ${PYTHON_IMAGE} AS runtime
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /nonexistent app
WORKDIR /srv/app
COPY --from=build /opt/venv /opt/venv
COPY alembic.ini ./
COPY migrations/ ./migrations/
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port \"${PORT:-8080}\""]
