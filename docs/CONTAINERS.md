# Local application containers

`compose.app.yaml` runs the built React frontend, the FastAPI API and a private
PostgreSQL database. It is separate from the native development database in
`compose.yaml`. No host database port or developer virtual environment is needed
for this stack. Nothing is deployed to Google Cloud or published to a registry.

## Start a persistent local demo

With Docker Desktop's Linux engine running, use the repository root:

```powershell
docker compose -f compose.app.yaml up --build -d --wait
docker compose -f compose.app.yaml run --rm --no-deps api python -m app.seed
```

Open <http://127.0.0.1:8080>. `DEMO_PORT` can select another host port. API requests
stay on the same browser origin and Nginx proxies them to the API's internal port
8080. Only the frontend is published, bound to host loopback. Its `/healthz`
checks the web server; the API container's `/health` checks application liveness,
not database readiness. Database health and the one-shot migration job gate
initial API startup. Stopping the demo preserves its named volume:

```powershell
docker compose -f compose.app.yaml down
```

The project is `pipeline-assistant-demo`, with its own `demo_data` volume and
fixed, clearly local-only database credentials. It does not connect to or migrate
the native development database. `app.seed` is an explicit idempotent command;
API startup never seeds data. The fictional runs retain the existing fixed
dates, so they will eventually age out of short performance windows.

The stack does not load `backend/.env`, copy local credentials, or pass a model
key. The public dashboard remains read-only. Reviewer access is disabled unless
the operator explicitly sets a separate `DEMO_REVIEWER_ACCESS_TOKEN` before
starting the stack. Do not use `VITE_*` variables for credentials. No model calls
are enabled by this Compose setup, even when reviewer access is configured.

## Image contents

The backend Dockerfile builds a virtual environment in a builder stage, then
copies it into the matching Python 3.15.2 slim runtime. Application code is
installed as a package; Alembic files are copied separately so the same image
can run an explicit migration or seed command. Runtime UID/GID are 10001.
`PORT` defaults to 8080 and the command uses `exec`, allowing Uvicorn to receive
shutdown signals directly. The API image installs core dependencies only; the
optional Google ADK/Pub/Sub worker image and unattended scheduling remain
deployment work. Existing API submission, review and status code does not import
the SDKs until generation is actually requested by a worker.

The frontend builder uses Node 24.22.2 and `npm ci`, then typechecks/builds the
React application. Its Nginx runtime contains only the generated static files
and proxy template; Node, npm, TypeScript sources and browser tests stay in the
builder stage. Tests are copied into that stage because the current tsconfig
includes their TypeScript checks. Runtime UID/GID are 101.

Python, Node and Nginx base tags are pinned to registry-verified manifest digests.
Update tags and digests together as dependencies receive fixes. PostgreSQL retains
the existing major-version policy (`postgres:17`). The frontend dependency tree
is locked by `package-lock.json`; Python's direct dependencies are pinned, while
its transitive dependencies are not yet locked. The image digests alone do not
make every future dependency resolution identical.

Each build context uses an allowlist `.dockerignore`. Backend inputs are the
package source, pyproject and migration files; frontend inputs are its manifest,
lock, config, source, public assets, tests and proxy template. `.env` files,
virtual environments, local draft/evaluation output, node_modules, tool downloads
and Git metadata are not build inputs. No secrets are supplied as build args or
baked into image environment defaults. Docker documents why build arguments
[should not carry secrets](https://docs.docker.com/build/building/secrets/).

## Local isolation and reverse proxy

API and web containers use read-only root filesystems, drop Linux capabilities
and prohibit privilege elevation. Writable temporary paths are explicit tmpfs
mounts with the runtime user's ownership. Nginx also receives a writable tmpfs
for its generated config. Database data lives in its separate volume. The API
and database share an internal Docker network; the web server joins both that
network and a frontend network. The API has no public host port or default
outbound route. This network layout is for the local read-only demo, not the
future cloud worker's outbound provider access.

The Nginx template substitutes only `PORT`, `API_UPSTREAM` and `NGINX_RESOLVER`.
Other Nginx variables such as `$uri` and `$http_authorization` are preserved.
The defaults target the local `api` service and Docker DNS (127.0.0.11). Resolving
the API dynamically lets the proxy recover when a container is replaced.

`/api/` forwards paths/query strings and Authorization headers to FastAPI.
Buffering, caching and automatic upstream retries are disabled; the read timeout
is 70 seconds, above the application's 45-second SSE connection target. This
preserves immediate events and avoids a proxy retrying operator writes. The
existing client remains responsible for bounded stream reconnection. Dotfiles,
missing hashed assets and unknown API paths do not fall through to index.html.
Other frontend paths receive the SPA entry point.

The entry point uses `Cache-Control: no-cache`; hashed `/assets/` files are
immutable with a one-year cache lifetime. Browser security headers include
nosniff, frame denial and a same-origin CSP. Inline styles remain permitted for
React's computed chart positions. The CSP is for the built app; the Vite dev
server has its own development behavior. Access logs omit query strings and
request headers. Private API responses preserve their existing no-store headers.

## Automated smoke test

Any recent Python with its standard library can run the check from the root;
no Gemini key, GitHub account or Google Cloud setup is required:

```powershell
python scripts/smoke_containers.py
```

Or use the existing project interpreter:

```powershell
.\backend\.venv\Scripts\python.exe scripts/smoke_containers.py
```

The script checks engine availability with a 15-second timeout, builds both
images, selects a free loopback port and starts a UUID-named temporary Compose
project. The demo database, volume and credentials are isolated from the normal
demo and development stacks. It creates an ephemeral fake reviewer token only
in subprocess memory, without printing or saving it. No real secret is needed.

Checks cover migrations, explicit seeding, frontend HTML/hashed asset serving,
real API/database reads, performance/report routes, operator authentication,
disabled paid generation, immediate SSE frame delivery through Nginx, non-root
runtime identities and absent local evidence files. Replacing the API verifies
that the database survives and Docker DNS resolution recovers. This is an HTTP
and container boundary test; it does not run the full Playwright suite against
the production web server.

Finally, it removes containers and volumes belonging only to its generated
`pipeline-assistant-smoke-<UUID>` project. Persistent demo/development volumes
are outside that namespace. Local build images/cache are retained. `--skip-build`
reuses previously built local images. The CI `containers` job runs the same
script on Ubuntu and does not push images or contact model/GitHub APIs.

Verification status (8 November 2026): both application images built and the
runtime smoke passed locally through Ubuntu WSL against Docker Desktop's Linux
engine. Checks passed for migrations/seeding, HTML/assets and security headers,
API/database reads, reviewer authorization, disabled model generation, immediate
SSE delivery, non-root runtime users, local-file isolation, and persistence/proxy
recovery after API replacement. Compose configuration and script syntax also
pass; the three application base-image tags/digests were verified in the public
registry. The CI job is added but has not been run remotely. The Windows client
connection remains a separate local issue, with the workaround below.

### Windows client connection workaround

On this Windows installation, Docker Desktop 4.21.1 reported **Engine running**
while both Windows named-pipe endpoints timed out. A direct ping to the Linux
engine's shared socket succeeded. The installed Ubuntu WSL client could reach
Docker 24.0.2 using that socket and API version 1.43. From PowerShell, the
equivalent smoke command for this checkout is:

```powershell
wsl.exe -d Ubuntu -u root --cd /mnt/c/Learning/Projects/pipeline-assistant -e env DOCKER_HOST=unix:///mnt/wsl/docker-desktop/shared-sockets/guest-services/docker.sock DOCKER_API_VERSION=1.43 python3 scripts/smoke_containers.py
```

These environment overrides apply only to that command. WSL root is used to
access the root-owned Docker socket; the API and web containers still run as
their unprivileged image users. This is a workaround for the observed local
client problem, not a required application setting or a repair of the Windows
connection. No Docker data reset, distribution removal or global Docker/PATH
configuration change is needed to use it.

## Deployment work still outstanding

Cloud Run configuration, worker packaging/scheduling, Artifact Registry,
managed PostgreSQL and migration execution, IAM, Secret Manager, production
upstream TLS/authentication, dependency locking/update policy, access/rate/cost
limits, logs/alerts and live public verification remain part of milestone 12.
The local HTTP proxy and fixed database credentials are not cloud deployment
configuration. Database-dependent readiness and real platform timeout/buffering
behavior also need deployment-specific checks before publishing the demo.
