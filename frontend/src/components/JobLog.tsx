import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchJobLog, JobNotFoundError } from '../api/logs'
import { SourceTime } from './SourceTime'

export function JobLog({ runId, jobId }: { runId: number; jobId: number }) {
  const [expanded, setExpanded] = useState(false)
  const query = useQuery({
    queryKey: ['job-log', runId, jobId],
    queryFn: ({ signal }) => fetchJobLog(runId, jobId, signal),
    enabled: expanded,
    retry: (failureCount, error) => !(error instanceof JobNotFoundError) && failureCount < 1,
  })
  const missing = query.error instanceof JobNotFoundError
  const excerpt = missing ? undefined : query.data

  return (
    <div className="border-t border-slate-100 p-4">
      <button
        type="button" className="button text-xs" aria-expanded={expanded}
        aria-controls={`job-${jobId}-log`} onClick={() => setExpanded(!expanded)}
      >
        {expanded ? 'Hide log excerpt' : 'View log excerpt'}
      </button>
      {expanded && (
        <section id={`job-${jobId}-log`} aria-label="Log excerpt" aria-busy={query.isFetching} className="mt-4 text-xs">
          {query.isPending ? (
            <p role="status" className="text-slate-500">Loading log excerpt…</p>
          ) : missing ? (
            <p role="alert" className="text-amber-900">This job is no longer available. Close this panel and refresh the run list.</p>
          ) : (
            <>
              {query.isError && (
                <div role="alert" className="mb-3 rounded-md bg-amber-50 p-3 text-amber-900">
                  <p>{excerpt ? 'Couldn’t refresh the log. Showing previously loaded evidence.' : 'We couldn’t load the log excerpt.'}</p>
                  <button className="button mt-2" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Retry log</button>
                </div>
              )}
              {excerpt === null && <p className="text-slate-500">No curated log excerpt is available for this job.</p>}
              {excerpt && (
                <>
                  <p className="leading-5 text-slate-500">
                    {excerpt.source_kind === 'demo' ? 'Fictional demo evidence.' : 'Curated GitHub evidence.'}
                    {' '}{excerpt.total_source_lines} source lines. {excerpt.truncated ? 'Partial excerpt; some content was omitted.' : 'Complete excerpt.'}
                    {excerpt.redactions_applied && ' Credential patterns were masked.'}
                  </p>
                  <p className="mt-1 text-slate-500">Stored <SourceTime value={excerpt.updated_at} /></p>
                  {excerpt.lines.length === 0 ? <p className="mt-3 text-slate-500">The stored log is empty.</p> : (
                    <div className="mt-3 overflow-hidden rounded-md bg-slate-950 p-3 text-slate-100">
                      <ol aria-label="Log source lines" className="space-y-1 font-mono text-xs leading-5">
                        {excerpt.lines.map((line) => (
                          <li key={line.source_line} className="flex gap-3">
                            <span aria-label={`Source line ${line.source_line}`} className="w-10 shrink-0 select-none text-right text-slate-400">{line.source_line}</span>
                            <code className="min-w-0 whitespace-pre-wrap break-all">{line.text || '\u00a0'}</code>
                          </li>
                        ))}
                      </ol>
                    </div>
                  )}
                </>
              )}
              <button className="button mt-3" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh log</button>
            </>
          )}
        </section>
      )}
    </div>
  )
}
