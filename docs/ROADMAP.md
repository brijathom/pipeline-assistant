# Completion checklist

Baseline: 8 November 2026. This tracks the original full project scope.
The twelve milestones below stay fixed; record new ideas separately before
changing scope. Milestones differ in effort, so their count is not an effort
percentage. A milestone is complete only when its listed outcomes are verified.

- [x] **1. Development foundation and CI:** reproducible local setup, repository
  conventions, backend tests and frontend build in GitHub Actions.
- [x] **2. Persistence and read APIs:** PostgreSQL models, migrations, run list and
  details, repository filters, guarded database integration tests.
- [x] **3. Public dashboard:** React run browsing, reliability counts, details,
  loading/error states and mobile/browser checks.
- [x] **4. GitHub ingestion:** bounded, idempotent, read-only imports of public
  runs, attempts, jobs, steps and processed logs; no workflow mutation.
- [x] **5. Evidence-backed investigations:** curated evidence snapshots, bounded
  redacted excerpts, exact citations, deterministic reports and stale-data hiding.
- [x] **6. Live AI draft generation:** bounded Google ADK invocation, validated
  structured output, provider failures handled safely, one real demo smoke test.
- [x] **7. Local review and publication:** private draft storage, final review
  decisions, integrity/freshness checks, explicit approved publication and AI label.
- [x] **8. Protected web review:**
  - [x] Single-operator protected draft inbox and private report viewer; no token
    persistence, private cache isolation and authorization tests.
  - [x] Approve/reject through the UI with a server-assigned operator identity.
  - [x] Explicit protected publication through the UI and conflict/error handling.
  - [x] Operator-controlled review/curation of imported real log evidence.
- [x] **9. AI quality evaluation:** repeatable cases for diagnostics, incomplete
  evidence and adversarial logs; recorded factuality/citation/safety results,
  including real curated examples and explicit limits on live evaluation calls.
  - [x] Versioned local corpus, deterministic rubric, rule baseline and CI gate.
  - [x] Saved AI draft assessment and explicit bounded live-evaluation command.
  - [x] Inspected real GitHub success example and a three-request live comparison
    with recorded response assessment. Application log curation remains explicit;
    a real failed-job example is future coverage when one is available.
- [ ] **10. Asynchronous investigations:** Pub/Sub worker, persistent task state,
  idempotent delivery handling, bounded retries and SSE progress/reconnection.
  - [x] Durable task state, protected queue/status APIs, one-attempt local worker,
    duplicate handling and operator status polling.
  - [x] Pub/Sub dispatch/consumption and bounded delivery recovery, verified with
    Google's local emulator and fake AI. Cloud clients use ADC; actual cloud IAM
    verification remains part of deployment in milestone 12.
  - [ ] Authenticated SSE progress and reconnection.
- [ ] **11. Performance metrics:** defined duration and time-window calculations,
  repository-level trends and useful dashboard presentation with appropriate tests.
- [ ] **12. Public deployment and production checks:** Docker images, Cloud Run,
  managed PostgreSQL/migrations, IAM/secrets, logs/health checks, access/rate/cost
  controls, deployment CI, public demo and documented operational trade-offs.

Current next step: authenticated SSE progress. Cloud IAM verification belongs
with deployment; the local Pub/Sub round trip is verified.
The local task workflow is documented in
`docs/ASYNC_INVESTIGATIONS.md`; milestone 10 remains open.

BigQuery and additional identity providers are optional choices, not completion
requirements. Multi-user accounts are outside this personal single-operator
baseline; adding them would require an explicit scope change.
