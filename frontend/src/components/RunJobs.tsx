import { useQuery } from '@tanstack/react-query'

import { fetchRunJobs } from '../api/jobs'
import { RunNotFoundError } from '../api/runs'
import { readable } from '../format'
import { JobLog } from './JobLog'
import { ResultBadge } from './ResultBadge'
import { SourceTime } from './SourceTime'

function failed(conclusion: string | null): boolean {
  return conclusion === 'failure' || conclusion === 'timed_out'
}

export function RunJobs({ runId }: { runId: number }) {
  const query = useQuery({
    queryKey: ['run-jobs', runId],
    queryFn: ({ signal }) => fetchRunJobs(runId, signal),
    retry: (failureCount, error) => !(error instanceof RunNotFoundError) && failureCount < 1,
  })
  const missingRun = query.error instanceof RunNotFoundError

  return (
    <section className="mt-8 border-t border-slate-200 pt-6" aria-labelledby="run-jobs-heading" aria-busy={query.isFetching}>
      <h3 id="run-jobs-heading" className="text-base font-semibold text-slate-900">Jobs and steps</h3>
      <p className="mt-2 text-xs leading-5 text-slate-500">Stored evidence for this attempt. Times are in UTC.</p>
      {query.isPending ? (
        <p role="status" className="mt-4 text-sm text-slate-500">Loading job evidence…</p>
      ) : missingRun ? (
        <p role="alert" className="mt-4 text-sm text-amber-900">This run is no longer available. Close this panel and refresh the run list.</p>
      ) : (
        <>
          {query.isError && (
            <div role="alert" className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">
              <p>{query.data ? 'Couldn’t refresh jobs. Showing previously loaded evidence.' : 'We couldn’t load job evidence.'}</p>
              <button className="button mt-3" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Retry jobs</button>
            </div>
          )}
          {query.data?.length === 0 && (
            <div className="mt-4 rounded-lg border border-dashed border-slate-300 p-4">
              <h4 className="text-sm font-medium text-slate-800">No stored jobs yet</h4>
              <p className="mt-2 text-xs leading-5 text-slate-500">No job evidence is stored for this attempt. Jobs will appear here after their evidence is imported.</p>
            </div>
          )}
          {query.data && query.data.length > 0 && (
            <div className="mt-4 space-y-4">
              {query.data.map((job) => {
                const failedSteps = job.steps.filter((step) => failed(step.conclusion)).length
                return (
                  <article key={job.id} className="overflow-hidden rounded-lg border border-slate-200 bg-white" aria-labelledby={`job-${job.id}-heading`}>
                    <div className="p-4">
                      <div className="flex flex-wrap items-start justify-between gap-3">
                        <h4 id={`job-${job.id}-heading`} className="min-w-0 flex-1 break-all text-sm font-semibold text-slate-900">{job.name}</h4>
                        <ResultBadge conclusion={job.conclusion} />
                      </div>
                      <p className="mt-2 text-xs text-slate-500">Job #{job.github_job_id} · {readable(job.status)}</p>
                      <dl className="mt-3 space-y-2 text-xs">
                        {([['Started', job.started_at], ['Completed', job.completed_at]] as const).map(([label, value]) => (
                          <div key={label}>
                            <dt className="text-slate-500">{label}</dt>
                            <dd className="mt-0.5 text-slate-700"><SourceTime value={value} /></dd>
                          </div>
                        ))}
                      </dl>
                    </div>
                    {job.steps.length === 0 ? (
                      <p className="border-t border-slate-100 px-4 py-3 text-xs text-slate-500">No stored steps for this job.</p>
                    ) : (
                      <details className="border-t border-slate-100" open={failed(job.conclusion) || failedSteps > 0}>
                        <summary className="cursor-pointer px-4 py-3 text-xs font-semibold text-slate-700">
                          {job.steps.length} stored {job.steps.length === 1 ? 'step' : 'steps'}{failedSteps > 0 && ` · ${failedSteps} failed`}
                        </summary>
                        <ol className="space-y-3 px-3 pb-3" aria-label={`Steps for ${job.name}`}>
                          {job.steps.map((step) => (
                            <li key={step.id} className={`rounded-md border-l-2 p-3 ${failed(step.conclusion) ? 'border-rose-400 bg-rose-50/60' : 'border-slate-200 bg-slate-50'}`}>
                              <div className="flex flex-wrap items-start justify-between gap-2">
                                <h5 className="min-w-0 flex-1 break-all text-xs font-semibold text-slate-800">Step {step.step_number}: {step.name}</h5>
                                <ResultBadge conclusion={step.conclusion} />
                              </div>
                              <p className="mt-2 text-xs text-slate-500">{readable(step.status)}</p>
                              <dl className="mt-2 space-y-2 text-xs">
                                {([['Started', step.started_at], ['Completed', step.completed_at]] as const).map(([label, value]) => (
                                  <div key={label}>
                                    <dt className="text-slate-500">{label}</dt>
                                    <dd className="mt-0.5 text-slate-700"><SourceTime value={value} /></dd>
                                  </div>
                                ))}
                              </dl>
                            </li>
                          ))}
                        </ol>
                      </details>
                    )}
                    <JobLog runId={runId} jobId={job.id} />
                  </article>
                )
              })}
            </div>
          )}
        </>
      )}
    </section>
  )
}
