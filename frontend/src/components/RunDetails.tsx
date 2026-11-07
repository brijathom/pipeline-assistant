import { useEffect, useRef } from 'react'
import { useIsFetching, useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchRun, RunNotFoundError } from '../api/runs'
import { readable } from '../format'
import { RunJobs } from './RunJobs'
import { SourceTime } from './SourceTime'

export function RunDetails({ runId, onClose }: { runId: number; onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const queryClient = useQueryClient()
  const jobsFetching = useIsFetching({ queryKey: ['run-jobs', runId], exact: true }) > 0
  const query = useQuery({
    queryKey: ['run', runId],
    queryFn: ({ signal }) => fetchRun(runId, signal),
    retry: (failureCount, error) => !(error instanceof RunNotFoundError) && failureCount < 1,
  })

  useEffect(() => {
    const dialog = dialogRef.current!
    dialog.showModal()
    return () => dialog.close()
  }, [])

  const run = query.data
  const notFound = query.error instanceof RunNotFoundError

  return (
    <dialog
      ref={dialogRef}
      className="run-details"
      aria-labelledby="run-details-heading"
      onClose={(event) => {
        // Strict Mode closes and reopens the dialog during its effect check.
        if (!event.currentTarget.open) onClose()
      }}
    >
      <div className="sticky top-0 z-10 flex items-center justify-between gap-4 border-b border-slate-200 bg-white px-5 py-5 sm:px-7">
        <h2 id="run-details-heading" className="text-base font-semibold text-slate-900">Run details</h2>
        <button className="button" type="button" onClick={() => dialogRef.current?.close()}>Close details</button>
      </div>
      <div className="p-5 sm:p-7" aria-busy={query.isFetching}>
        {query.isPending ? (
          <p role="status" className="text-sm text-slate-500">Loading run details…</p>
        ) : notFound ? (
          <div role="alert">
            <h3 className="font-semibold text-slate-900">Run not found</h3>
            <p className="mt-2 text-sm leading-6 text-slate-500">This run is no longer available in the stored data. Close this panel and refresh the run list.</p>
          </div>
        ) : (
          <>
            {query.isError && (
              <div role="alert" className="mb-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs leading-5 text-amber-900">
                <p>{run ? 'Couldn’t refresh details. Showing previously loaded data.' : 'We couldn’t load run details.'}</p>
                <button className="button mt-3" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Retry details</button>
              </div>
            )}
            {run && (
              <>
                <h3 className="break-words text-xl font-semibold tracking-tight text-slate-900">{run.workflow_name}</h3>
                {run.repository.startsWith('demo/') && <p className="mt-3 text-xs text-indigo-700">Demo data · This is a fictional workflow run.</p>}
                <dl className="mt-6 divide-y divide-slate-100">
                  {[
                    ['Repository', run.repository], ['GitHub run', `#${run.github_run_id}`],
                    ['Attempt', run.run_attempt], ['Status', readable(run.status)],
                    ['Conclusion', run.conclusion ? readable(run.conclusion) : 'Not available'],
                  ].map(([label, value]) => (
                    <div key={label} className="py-3">
                      <dt className="text-xs text-slate-500">{label}</dt>
                      <dd className="mt-1 break-all text-sm font-medium text-slate-800">{value}</dd>
                    </div>
                  ))}
                  {[
                    ['Created', run.created_at], ['Started', run.started_at], ['Completed', run.completed_at],
                  ].map(([label, value]) => (
                    <div key={label} className="py-3">
                      <dt className="text-xs text-slate-500">{label}</dt>
                      <dd className="mt-1 text-sm text-slate-800">
                        <SourceTime value={value} />
                      </dd>
                    </div>
                  ))}
                </dl>
                <p className="mt-5 text-xs leading-5 text-slate-500">Stored snapshot. Missing timestamps are shown as unavailable.</p>
                <button className="button mt-4" type="button" onClick={() => void Promise.all([
                  query.refetch(),
                  queryClient.refetchQueries({ queryKey: ['run-jobs', runId], exact: true }),
                ])} disabled={query.isFetching || jobsFetching}>
                  {query.isFetching || jobsFetching ? 'Refreshing details…' : 'Refresh details'}
                </button>
                <RunJobs runId={runId} />
              </>
            )}
          </>
        )}
      </div>
    </dialog>
  )
}
