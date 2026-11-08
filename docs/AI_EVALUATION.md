# Investigation quality evaluation

The evaluator compares reports against explicit evidence and small scenario
expectations. It runs locally and never approves, publishes or changes a stored
run. It does not need a database for rule-baseline or saved-draft evaluation.

## Run the baseline

From `backend`:

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_ai
```

The default is `--mode rules`: it runs the actual deterministic report builder
against the versioned corpus. It does not load `.env`, call Gemini or require
the optional AI dependencies. To select cases and retain a result:

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_ai --case http-503 --case log-injection --output .evals/baseline-check.json
```

Result files must be new; existing files are preserved. `.evals/` is ignored by
Git. JSON output contains check results, counts, versions and SHA-256 fingerprints,
with no report text, evidence text, credentials or local input paths. CI runs the
complete rule baseline after Pytest; a failing check fails the step.
Each check records its own `passed` and `evaluated` counts. Conditional checks,
such as partial-evidence disclosure, count only cases where that condition applies.

Exit codes are `0` for all selected checks passing, `1` for a failed check or
incomplete evaluation, and `2` for invalid arguments, configuration or files.

## Check an existing AI draft

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_ai --mode saved --draft .drafts/YOUR_DRAFT.json --output .evals/saved-check.json
```

This validates the saved draft's schema, integrity, evidence fingerprint and exact
citations, then applies general report checks to its saved snapshot. It does not
contact Gemini, query the current database or modify the draft. A current-evidence
check still occurs when approving or publishing through the application.

Saved mode has no scenario-specific labels, so finding-count and expected-term
checks are not reported. It records the model and prompt version from the file.
The original instruction text was not stored with older drafts, so its hash is
`null`. Saved mode also has no corpus version/hash: its input is the saved snapshot.

## Optional live evaluation

Live mode sends only explicitly selected corpus cases to the configured Gemini
model through the production Google ADK boundary. It can use provider quota or
incur charges according to the existing account configuration. Run it only when
you intend to make those calls; local baseline tests and CI never enable it.

```powershell
.\.venv\Scripts\python.exe -m app.evaluate_ai --mode live --case http-503 --case log-injection --max-calls 2 --output .evals/live-check.json
```

Both `--case` and `--max-calls` are required. The hard budget is at most five
selected cases per invocation. Duplicate selections and selections exceeding the
budget are rejected before generation. Calls run sequentially, once per case,
without automatic retries. Generation stops after the first provider/schema/
citation failure; the remaining cases are reported as incomplete through the
selected/completed counts. Quality-check failures still allow the explicitly
budgeted remaining cases to run.

The existing limits remain in force: one ADK model call, a 64 KiB input bound,
8,192 output tokens, a 128 KiB response bound, a 60-second generation deadline,
no tools and no access to CI write operations. Empty evidence is rejected before
provider-client creation and appears as `blocked_no_evidence`, with zero model
attempts. It is counted separately from graded responses.

No complete model response is saved by this evaluator. Its results identify
check failures; deeper manual review requires a separately generated private
draft through the existing draft-generation command.

## Corpus and checks

[`evaluation_cases.py`](../backend/app/evaluation_cases.py) defines corpus
`investigations-v1`. Each case includes provenance and explicit expected finding
bounds. The corpus has 15 cases:

| Category | Cases |
| --- | --- |
| Diagnostics | HTTP 503 assertion, timeout, compiler error, Unicode/source numbering, different results across jobs |
| Incomplete evidence | Partial excerpt, excluded failed jobs |
| Insufficient evidence | No diagnostic, empty log, no curated jobs |
| Adversarial input | Instructions in log text, instructions in job name, instructions without diagnostics |
| Redaction | Already-masked credential beside a diagnostic |
| Recorded local failure | Exact `TypeError` diagnostic captured during local project tests |

The recorded local excerpt was inspected before inclusion; paths and surrounding
traceback were omitted and the excerpt is labelled partial. Its provenance does
not represent a GitHub Actions capture. The other cases are explicitly synthetic.
Real imported GitHub examples remain subject to the protected operator-curation
workflow before being used or shared as evaluation fixtures.

[`evaluation.py`](../backend/app/evaluation.py) defines rubric `report-checks-v1`:

- **Format and citations:** canonical report schema, response-size bound and exact
  job/name/source-line/complete-quote matching.
- **Narrow observation checks:** HTTP status numbers in each observation must match
  that finding's assertion quote. Scenario cases also have finding-count bounds
  and selected required observation terms.
- **Evidence limits:** disclose partial excerpts and excluded jobs; produce no
  findings when no source lines are available.
- **Narrative heuristics:** flag the corpus injection marker, known credential
  patterns, several unsafe-action phrases and several claims of proven cause.

Policy checks examine summary, findings and limitations. They deliberately exclude
literal citation quotes and job names: repeating a quoted instruction as evidence
is different from following it. Those source fields still require operator review.

## Recorded results and interpretation

[`evaluation-results.json`](evaluation-results.json) records the first verified
run on 8 November 2026:

- Rule baseline: **15/15** cases passed the configured minimum checks.
- Previously generated Gemini 3.5 Flash Lite demo draft: **1/1** passed the
  applicable general checks against its fictional saved evidence snapshot.
- New live provider calls made during these runs: **0**.

The corpus exposed a real baseline defect: exception names such as `TypeError`
were missed by a matcher looking for the standalone word `Error`. The matcher now
recognises exception names ending in `Error` or `Exception`, and the recorded
local failure is a regression case.

These results are a regression baseline and one saved-response assessment. They
are not a model accuracy percentage, a prompt-injection resistance measurement or
proof of production safety. A term check can miss reversed expected/actual roles;
keyword policy checks can miss paraphrases and flag safe negated wording. Compiler
diagnostics currently require a cited finding, without grading a detailed causal
explanation. A model that produces generic but compliant advice may pass.

Next, an operator should curate a real imported GitHub example and perform a
small, explicitly budgeted live comparison. Manual review should check each
observation against its quote, distinguish hypotheses from established facts,
assess whether the proposed read-only check is useful, and check that injected
instructions did not change the report's purpose. Record failures as well as
passes and keep provider failures separate from report-quality failures.
