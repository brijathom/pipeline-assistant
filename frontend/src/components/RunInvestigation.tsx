import { useQuery } from '@tanstack/react-query'

import { fetchInvestigation } from '../api/investigations'
import { RunNotFoundError } from '../api/runs'
import { InvestigationReportContent } from './InvestigationReportContent'

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
          {data?.status === 'stale' && <p role="status" className="mt-3 text-xs leading-5 text-amber-900">The stored report can no longer be verified. It is hidden until a current investigation is available.</p>}
          {data?.status === 'ready' && data.report && (
            <div className="mt-3 space-y-4 text-xs leading-5">
              <p className="font-medium text-indigo-700">{
                data.generation_method === 'rules-v1' ? 'Rule-based evidence summary · No AI model was called.'
                  : data.generation_method === 'google-adk-v1' ? 'AI-generated investigation · Approved draft'
                    : 'Stored investigation'
              }</p>
              <InvestigationReportContent report={data.report} generatedAt={data.generated_at} />
            </div>
          )}
          <p className="mt-4 text-xs leading-5 text-slate-500">Recommendations are advisory. This report does not change or rerun workflows.</p>
          <button type="button" className="button mt-3" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh investigation</button>
        </>
      )}
    </section>
  )
}
