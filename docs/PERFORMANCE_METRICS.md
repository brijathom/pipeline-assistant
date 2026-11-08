# Job performance metrics

The public dashboard shows median and p95 **job execution duration**, coverage,
and daily trends for a repository or all stored repositories. Existing reliability
counts remain an all-time view of stored attempts. The performance window is a
separate, explicitly labelled selection; it does not filter the recent run table.

## Why job durations

GitHub's workflow-run list supplies `run_started_at` but no reliable completion
timestamp. The importer deliberately leaves run `completed_at` unset rather than
using `updated_at`. Jobs supply explicit start and completion timestamps. These
support real duration measurements without inventing a workflow finish time.
See GitHub's [workflow run API](https://docs.github.com/en/rest/actions/workflow-runs)
and [workflow job API](https://docs.github.com/en/rest/actions/workflow-jobs).

A job's measured duration is `completed_at - started_at`, in seconds. It is
elapsed wall time during that job, not CPU time or billed minutes. It excludes
queueing before the job starts. Two parallel jobs are two observations: their
durations are neither summed nor presented as total pipeline duration. Queue
time, critical-path analysis and end-to-end workflow duration are not inferred.

## Window and sample definitions

`GET /api/metrics/performance?days=30&repository=owner/name` is public and read-only.
`days` accepts only 7, 30 or 90, default 30; repository validation/normalization
matches the existing run and reliability APIs. An unknown repository returns
zero counts, null percentiles and the requested complete date series.

The server captures one UTC `end_at` for the response. `start_at` is UTC midnight
`days - 1` dates before today. Include stored attempts whose `created_at` lies
in **[start_at, end_at)**. Thus “Last 7 days” means six complete preceding UTC
dates plus today so far, rather than a rolling 168-hour period. Today is partial.
Neither browser timezone nor database session timezone changes the boundaries.

Join the stored jobs belonging to those attempts. Bucket each job by its parent
attempt's creation date in UTC, not the job's start or completion day. A job that
finishes the following day remains in its creation cohort. Refreshing an
unfinished cohort can add timing observations as imports update stored jobs.
Each rerun attempt remains separate. GitHub can retain the original run creation
date on reruns, so a later rerun may appear in that original date's cohort; this
view is not a calendar of when runners consumed resources.

`total_jobs` counts stored jobs in the cohort. A job contributes to `timed_jobs`
and duration statistics only when all of these hold:

- Its status is `completed` and its conclusion is not `skipped`.
- Start and completion timestamps are both present.
- Start is at or after the attempt's stored creation time.
- Completion is at or after start, and no later than `end_at`.

All other stored jobs contribute to `excluded_jobs = total_jobs - timed_jobs`.
Missing, inconsistent, unfinished and future timings never become zero samples.
A genuine zero-second completed job is retained. Completed failed, timed-out,
cancelled and unknown-outcome jobs can contribute when their timings are valid;
these durations describe observed execution rather than success-only speed.
The parent attempt may still be in progress when an individual job finishes.

Coverage is relative to **stored jobs**, not every job in GitHub. An attempt with
no imported job metadata adds no observations. No read initiates an import or
contacts GitHub. Missing dates are zero-filled for counts, but retain **null**
percentiles; a gap in the chart does not mean zero execution time.

## Percentiles and presentation

PostgreSQL's [`percentile_cont`](https://www.postgresql.org/docs/17/functions-aggregate.html)
computes the median (p50) and p95 over valid duration values with linear
interpolation. The result need not equal an observed duration. For 60 and 600
seconds, the median is 330 and p95 is 573 seconds. An empty sample yields null.
The overall percentile is calculated over all individual jobs, not an average
of daily percentiles. Each job has equal weight; large matrices contribute more
observations than small workflows. Changes in job/workflow mix can change the
repository percentile even if each individual job stays the same speed.

The API returns seconds at floating-point precision. The UI rounds for legibility
and distinguishes `Unavailable`, `0s` and positive values under one second (`<1s`).
The chart uses median bars and p95 markers. Native hover titles and an expandable
Daily values table provide daily counts and formatted durations. Days without
timings have gaps. Fewer than 20 timed jobs triggers a small-sample warning;
the threshold is a presentation caution, not a statistical confidence bound.
The existing fictional demo is small and therefore displays that warning.

Repository and window are part of the TanStack Query key. Changing either hides
the previous selection until its own result arrives; a late response cannot
overwrite the active selection. A failed background refresh may retain previous
public values with an explicit stale warning. Performance errors have an
independent retry control and do not hide runs or reliability counts. The main
Refresh button refreshes active performance data as well as the existing panels.

## Query and operational choices

One SQL aggregate uses [`ROLLUP`](https://www.postgresql.org/docs/17/queries-table-expressions.html#QUERIES-GROUPING-SETS)
to calculate daily groups and the overall sample in one database snapshot. It
returns at most 91 rows; Python fills missing dates without loading individual
jobs. A preceding `SET LOCAL statement_timeout = '5000ms'` bounds statement
execution. Database errors/timeouts roll back and return a safe 503. Pool or
connection acquisition time is separate from the statement timeout.

Migration `0009` adds B-tree indexes on `pipeline_runs.created_at` and
`(repository, created_at)` for the two window filters. The existing unique job
index `(pipeline_run_id, github_job_id)` already supports the join, so no
redundant foreign-key index is added. The planner may correctly prefer a
sequential scan on a small local dataset. These indexes do not make percentile
sorting free, and no production latency claim is inferred from a tiny demo.

Regular transactional index creation fits this small app. A large live table
would require planning an online/concurrent index migration to avoid blocking
writes. Query-plan inspection, real data volumes and service-wide rate/instance
limits remain deployment checks. PostgreSQL is sufficient for this bounded
personal dashboard; BigQuery and rollup/materialized tables would add complexity
before a measured scaling need exists.

## Verification

Guarded PostgreSQL tests cover empty/unknown repositories, interpolation,
unweighted individual-job percentiles, reruns, parallel jobs, exclusions, valid
zero values, future timestamps, exact window boundaries and UTC bucketing with
a non-UTC database timezone. Tests verify a single aggregate and no private
evidence/report/log reads. Migration rollback preserves stored runs.

Browser checks cover duration display, sample-size/exclusion labels, accessible
daily values, mobile width, repository/window cache separation, main refresh,
independent errors, stale values, loading, and late responses from old filters.
No tests call GitHub or a model provider.
