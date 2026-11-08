# Background investigations and Pub/Sub delivery

The protected inbox can queue an investigation and show its persisted status.
A separate worker creates an unreviewed draft. Tasks can be processed by UUID
or dispatched through Pub/Sub. The inbox currently uses status polling; SSE
progress is the next integration.

The task record is the source of truth. The web process never launches a model
request in memory after returning an HTTP response. This fits later delivery by
Pub/Sub without depending on the lifetime of the API process.

## Local use

Apply the additive migration from `backend`, then restart the backend:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Open **Reviewer inbox**, unlock it, and choose **Investigation tasks**. Select a
stored run and choose **Queue investigation**. The server requires configured
Gemini access and current curated failed-job evidence. The fictional failed run
`demo/pipeline-assistant #1002` is suitable. The newly imported successful real
run has no failed-job evidence and is intentionally rejected.

Queueing saves a task and snapshot without making a model call in the API. A
running Pub/Sub consumer may then process it using Gemini quota. Alternatively, copy the task
UUID from the list and run the separate worker from another terminal in `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.task_worker --task-id TASK_UUID
```

This explicit command can consume Gemini quota or incur charges under the existing
account settings. It attempts at most one bounded generation for this task.
While tasks are queued or running, the inbox refreshes the twenty most recent
statuses every two seconds. **Review drafts** opens the existing private review
view after success. Neither queueing nor processing approves or publishes a report.

## Lifecycle and request identity

```mermaid
stateDiagram-v2
    [*] --> queued: protected submission
    queued --> running: committed worker claim
    running --> succeeded: draft and status commit together
    running --> failed: generation or evidence failure
    running --> failed: explicit interrupted-task recovery
```

The browser generates one UUID for each submission. It sends that UUID as
`request_id` and the selected local `run_id` to `POST /api/reviewer/tasks`.
Reusing the same request UUID for the same run returns the original task;
reusing it for another run returns 409. An unconfirmed submission keeps its UUID
for an explicit retry. There are no automatic POST retries.

PostgreSQL locks the run while enqueueing. A partial unique index also enforces
one queued/running task per run. The snapshot includes only curated failed-job
evidence and is limited to the existing 64 KiB prompt budget. Model name, prompt
version and evidence fingerprint are stored with the task. The browser cannot
supply these values or an arbitrary snapshot.

`GET /api/reviewer/tasks` accepts a bounded `limit` (default 20, maximum 50).
`GET /api/reviewer/tasks/{uuid}` returns one status. Both omit snapshots and
report content. All routes require the existing reviewer bearer credential and
use private `no-store` response headers. Public endpoints remain read-only.

The run selector shows up to 100 recent public run summaries. The server remains
authoritative: a run can be displayed but lack curated failure evidence. Failed
status refreshes hide the previous task list; authorization failures lock the
inbox. Locking/closing discards private cache and credentials. An already accepted
task survives closing. If an unconfirmed request UUID was lost when closing,
inspect the persisted task list before submitting another request.

## Worker transactions and duplicate handling

1. Lock the task. Only `queued` may become `running`. Validate the stored snapshot,
   prompt version, model name and current evidence, then commit the claim.
2. Close the database session before awaiting the bounded Google ADK invocation.
3. Lock the task again. Validate the report and exact citations, recheck current
   evidence, and atomically store an unreviewed draft plus `succeeded` status.

A second delivery of a running or terminal task makes no model call. A stale or
invalid snapshot fails before calling the provider. Evidence that changes during
generation prevents the draft from being stored. Exceptions are represented by
fixed safe error codes; provider bodies, keys and traces are not persisted.

This protects one generation attempt per task, with a deliberate availability
trade-off. If a worker stops after committing its claim, the model may have
received a request even if no draft was saved. Automatically reclaiming that task
could charge twice. A failed final commit rolls back the draft and leaves the
task running; delivery of the same task still does not repeat generation.

For a running task older than 120 seconds, explicitly mark it interrupted:

```powershell
.\.venv\Scripts\python.exe -m app.task_worker --task-id TASK_UUID --mark-interrupted
```

This uses database time and does not call the provider. It cannot overwrite a
fresh or completed task. A late worker response cannot attach a draft once the
task is terminal. Inspect status before choosing a new submission: a new UUID
can make a new charged request. Interruption does not recall an already received
provider request. Failed tasks are never automatically retried.

There is no automatic queued-task expiry or cancellation. CLI commands below
process bounded batches and exit. A scheduler or supervised loop must invoke
them repeatedly for unattended processing; the API never starts these processes.

## Local Pub/Sub setup

Install the optional client from `backend`:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev,ai,pubsub]"
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Start Google's emulator from the repository root. This creates a local container,
not cloud resources; its port is bound only to loopback:

```powershell
docker compose --profile pubsub up -d pubsub
```

In each terminal that runs a Pub/Sub command, set the process environment:

```powershell
$env:PUBSUB_EMULATOR_HOST='127.0.0.1:8085'
```

The SDK reads this variable itself, so putting it only in `backend/.env` is
insufficient. Keep the default `PUBSUB_PROJECT_ID=pipeline-assistant-local`.
From `backend`, initialize the emulator once, queue a task in the inbox, then run:

```powershell
.\.venv\Scripts\python.exe -m app.pubsub_tasks init-emulator
.\.venv\Scripts\python.exe -m app.pubsub_tasks dispatch --limit 10
.\.venv\Scripts\python.exe -m app.pubsub_tasks work --limit 10
.\.venv\Scripts\python.exe -m app.pubsub_tasks status
```

`init-emulator` refuses any host other than localhost/127.0.0.1 port 8085 and
any project other than the local default, before constructing SDK clients.
Initialization is idempotent; existing subscriptions are checked rather than
silently reconfigured. Local topics/subscriptions disappear when the emulator
container is recreated. Dispatching saves no evidence in Pub/Sub: the exact
payload is a canonical 36-byte task UUID, with no credentials or report content.

`work` uses the configured Gemini key and **can consume quota or incur charges**.
The emulator replaces message transport, not the model. No command approves or
publishes reports. Each command handles up to 20 publications/messages (default
10) sequentially; the consumer exits after an empty/10-second timed-out pull.
Commands suppress private exception details and return nonzero on infrastructure
failure. The status command prints at most 50 task IDs and delivery metadata.

## Dispatch records and recovery

[Pub/Sub's default delivery is at least once](https://docs.cloud.google.com/pubsub/docs/subscription-overview),
so duplicate deliveries must be safe even when publishers or consumers restart.
Messages contain only a task UUID, with private evidence read from
PostgreSQL. They invoke the same committed claim/generation/finalization boundary.
Message redelivery and a new model attempt are separate decisions.

The task row is also a transactional outbox record, avoiding a separate table
and avoiding a database/message dual write inside the submission API. Migration
`0008` adds `dispatch_attempts`, `dispatch_after` and `dispatched_at`. Existing
queued tasks become eligible; terminal/running tasks are never dispatched.

The dispatcher locks one eligible row with `FOR UPDATE SKIP LOCKED`, increments
its attempt count, sets its next eligibility and commits **before** publication.
No database transaction spans the network call. Delays are 30, 60, 120, 240 and
480 seconds, with at most five persisted attempts. The SDK's own publish retries
are disabled and each RPC has a 10-second timeout. A confirmed publication is
recorded in a new transaction. If publication succeeded but confirmation or the
final database commit was lost, a later attempt may publish twice. The committed
worker claim still permits only one model attempt per task.

Five unconfirmed attempts leave the task queued and show a delivery-stopped
warning in the inbox. No sixth automatic publication occurs. Inspect `status`,
repair the transport and use `app.task_worker --task-id UUID` for explicit
recovery. This handles either a never-started task or a duplicate safely; inspect
any running task before the existing interrupted-task procedure. Repeating the
submission UUID never resets dispatch attempts. A new UUID is a new potential
charged task, so it is not a transport-recovery shortcut.

The pull consumer receives one message at a time, extends its acknowledgement
deadline to 120 seconds, and calls the same worker boundary. It acknowledges
successful, failed, already-running, terminal, missing or malformed tasks.
Infrastructure exceptions negatively acknowledge the message and stop the batch.
If acknowledgement itself is lost, redelivery reads persisted state and does not
repeat generation. Acknowledging an already-running task does not recover a
crashed worker; the explicit interrupted-task procedure still applies.

The subscription must use the configured topic, a deadline of at least 120
seconds, 10-to-60-second retry backoff, and the configured `-dead-letter` topic
with five maximum delivery attempts. The consumer checks these before pulling.
Google's dead-letter forwarding and delivery attempt counts are **best effort**,
not an exact five-delivery guarantee. Infrastructure failures before database
access rely on that subscription policy; the five-publication database limit
does not limit broker redeliveries. Dead-letter messages require operator
inspection and are never automatically replayed by this application.

## Cloud authentication and deployment boundary

[Google's Pub/Sub clients use Application Default Credentials](https://docs.cloud.google.com/pubsub/docs/authentication).
Pub/Sub does not use the Gemini API key or the reviewer bearer token. A pull
consumer needs no public inbound worker endpoint. On Google Cloud, use attached
service accounts and IAM, not a downloaded service-account key. Provision
resources outside these application commands: initialization is emulator-only.

The dispatcher needs `roles/pubsub.publisher` on the task topic. The worker
needs `roles/pubsub.subscriber` on its subscription plus
`pubsub.subscriptions.get` to validate configuration (a narrow custom role can
provide that permission). Use a separate operator identity for dead-letter
inspection. Configure the Pub/Sub service agent's required publisher permission
on the dead-letter topic and subscriber permission on the original subscription,
as described in [Google's dead-letter documentation](https://docs.cloud.google.com/pubsub/docs/dead-letter-topics).
The configuration check cannot prove those IAM bindings are effective.

Cloud resources, IAM integration, a scheduler/supervised worker, monitoring and
Cloud Run execution settings remain deployment work. These bounded pull commands
fit a job; a long-lived pull process needs suitable CPU allocation/lifecycle.
Do not run a permanent pull loop inside a request-driven Cloud Run API process.
Google generally recommends StreamingPull for higher-throughput consumers;
[synchronous pull](https://docs.cloud.google.com/pubsub/docs/pull) is used here to
keep this small sequential worker bounded and easy to shut down.

## SSE progress

SSE will stream persisted status changes with authenticated fetch streaming,
reconnection and bounded connection lifetimes. The reviewer token must remain
in the Authorization header, with no credential in a URL. SSE is not yet
implemented. No GCP resources were created during this local transport work.

## Verification

PostgreSQL tests cover request identity, one active task per run, duplicate
delivery during/after generation, unsafe/invalid reports, stale evidence,
ambiguous final commits and late-result fencing. An integration test drives the
real ADK runner with a fake local model and no provider traffic. Migration tests
verify rollback preserves runs, evidence, drafts and existing public reports.
Browser tests cover queueing, polling, explicit same-ID retry and private-data
cleanup. Transport tests cover exact UUID payloads, persisted delay/attempt
limits, duplicate publication, acknowledgement/nack decisions, local-only
initialization and subscription policy validation. None makes a paid model call.

An opt-in integration test uses Google's actual emulator, the guarded PostgreSQL
test database and a fake model:

```powershell
$env:TEST_DATABASE_URL='postgresql+psycopg://pipeline_test:local-test-only@127.0.0.1:5433/pipeline_assistant_test'
$env:RUN_PUBSUB_EMULATOR_TESTS='1'
.\.venv\Scripts\python.exe -m pytest -q tests/test_pubsub_emulator.py
Remove-Item Env:RUN_PUBSUB_EMULATOR_TESTS
```

It creates UUID-named local topics/subscriptions, dispatches one task, delivers a
deliberate duplicate, and checks only one fake model call/private pending draft.
It never reads the configured Gemini key. The emulator has no IAM support, so
this is not a cloud authentication test. The round trip passed using Google's
standalone emulator 0.8.37 on local Java 17 after Docker's engine timed out.
The official SDK component archive was checksum-verified and extracted only
under the ignored `.tools/pubsub-emulator` directory; it is not a repository
dependency. The temporary emulator was stopped after verification. Docker's
Compose startup and actual cloud IAM remain separately unverified. CI installs
the Pub/Sub dependency and runs boundary/database tests; the emulator test skips
unless explicitly enabled.
