import { useQuery } from '@tanstack/react-query'

import { fetchInvestigation } from '../api/investigations'
import { RunNotFoundError } from '../api/runs'
import { SourceTime } from './SourceTime'

export function RunInvestigation({ runId }: { runId: number }) {
  const query = useQuery({
    queryKey: ['run-investigation', runId],
    queryFn: ({ signal }) => fetchInvestigation(runId, signal),
    retry: (failureCount, error) => !(error instanceof RunNotFoundError) && failureCount < 1,
    // A failed refresh must not keep evidence that may have lost curation visible.
    staleTime: 0,
  })
  const data = query.isError ? undefined : query.data
  return (
    <section className="mt-8 border-t border-slate-200 pt-6" aria-labelledby="investigation-heading" aria-busy={query.isFetching}>
      <h3 id="investigation-heading" className="text-base font-semibold text-slate-900">Investigation</h3>
      {query.isPending ? <p role="status" className="mt-3 text-sm text-slate-500">Loading investigation…</p> : (
        <>
          {query.isError && (
            <div role="alert" className="mt-3 rounded-lg bg-amber-50 p-3 text-xs text-amber-900">
              <p>{query.error instanceof RunNotFoundError ? 'This run is no longer available.' : 'We couldn’t load or verify the investigation. Refresh to try again.'}</p>
              {!(query.error instanceof RunNotFoundError) && <button type="button" className="button mt-3" onClick={() => void query.refetch()} disabled={query.isFetching}>Retry investigation</button>}
            </div>
          )}
          {data?.status === 'not_generated' && <p className="mt-3 text-xs leading-5 text-slate-500">No investigation has been generated for this attempt.</p>}
          {data?.status === 'stale' && <p role="status" className="mt-3 text-xs leading-5 text-amber-900">The stored evidence has changed. The previous report is hidden until a new investigation is generated.</p>}
          {data?.status === 'ready' && data.report && (
            <div className="mt-3 space-y-4 text-xs leading-5">
              <p className="font-medium text-indigo-700">Rule-based evidence summary · No AI model was called.</p>
              <p className="text-slate-700">{data.report.summary}</p>
              <p className="text-slate-500">Generated <SourceTime value={data.generated_at} /></p>
              {data.report.findings.map((finding, index) => (
                <article key={index} className="rounded-lg border border-slate-200 p-4" aria-label={finding.title}>
                  <h4 className="font-semibold text-slate-900">{finding.title}</h4>
                  <p className="mt-2 text-slate-700">{finding.observation}</p>
                  <p className="mt-3 text-slate-500">{finding.citation.job_name} · Source line {finding.citation.source_line}</p>
                  <blockquote className="mt-2 whitespace-pre-wrap break-all rounded-md bg-slate-950 p-3 font-mono text-slate-100">{finding.citation.quote}</blockquote>
                  <h5 className="mt-3 font-semibold text-slate-800">Suggested next step</h5>
                  <p className="mt-1 text-slate-700">{finding.recommendation}</p>
                </article>
              ))}
              <div>
                <h4 className="font-semibold text-slate-800">Limitations</h4>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-slate-500">{data.report.limitations.map((limit, index) => <li key={index}>{limit}</li>)}</ul>
              </div>
            </div>
          )}
          <p className="mt-4 text-xs leading-5 text-slate-500">Recommendations are advisory. This report does not change or rerun workflows.</p>
          <button type="button" className="button mt-3" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh investigation</button>
        </>
      )}
    </section>
  )
}
