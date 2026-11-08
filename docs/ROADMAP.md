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
- [ ] **9. AI quality evaluation:** repeatable cases for diagnostics, incomplete
  evidence and adversarial logs; recorded factuality/citation/safety results,
  including real curated examples and explicit limits on live evaluation calls.
  - [x] Versioned local corpus, deterministic rubric, rule baseline and CI gate.
  - [x] Saved AI draft assessment and explicit bounded live-evaluation command.
  - [ ] Operator-curated GitHub example and a small live comparison with manual
    assessment; the recorded local failure and saved demo draft are initial evidence.
- [ ] **10. Asynchronous investigations:** Pub/Sub worker, persistent task state,
  idempotent delivery handling, bounded retries and SSE progress/reconnection.
- [ ] **11. Performance metrics:** defined duration and time-window calculations,
  repository-level trends and useful dashboard presentation with appropriate tests.
- [ ] **12. Public deployment and production checks:** Docker images, Cloud Run,
  managed PostgreSQL/migrations, IAM/secrets, logs/health checks, access/rate/cost
  controls, deployment CI, public demo and documented operational trade-offs.

Current next step: curate an imported GitHub example and record a small live
evaluation with manual assessment. The evaluation runner, offline baseline and
saved-draft checks are complete; milestone 9 remains open for that evidence.

BigQuery and additional identity providers are optional choices, not completion
requirements. Multi-user accounts are outside this personal single-operator
baseline; adding them would require an explicit scope change.
