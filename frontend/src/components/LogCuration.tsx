import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchPrivateLog, fetchPrivateLogs, ReviewerAccessError, setLogCuration } from '../api/reviewer'
import { SourceTime } from './SourceTime'

interface Props {
  token: string
  onUnauthorized: (message: string) => void
  onChanged: () => void
}

export function LogCuration({ token, onUnauthorized, onChanged }: Props) {
  const [curated, setCurated] = useState(false)
  const [selected, setSelected] = useState<number | null>(null)
  const query = useQuery({ queryKey: ['reviewer-logs', curated],
    queryFn: ({ signal }) => fetchPrivateLogs(token, signal, curated) })
  useEffect(() => {
    if (query.error instanceof ReviewerAccessError) onUnauthorized(query.error.message)
  }, [query.error, onUnauthorized])
  return <section aria-label="Imported log review" className="space-y-4">
    <h3 className="text-sm font-semibold text-slate-900">Imported log excerpts</h3>
    <p className="text-xs leading-5 text-slate-600">Only stored, processed GitHub excerpts appear here. Automatic redaction can miss sensitive details. Review every displayed line before allowing public access or use in investigations.</p>
    <div className="flex flex-wrap items-center gap-3">
      <label htmlFor="log-curation-filter" className="text-xs font-semibold text-slate-700">Curation status</label>
      <select id="log-curation-filter" className="rounded-lg border border-slate-300 bg-white p-2"
        value={String(curated)} onChange={(event) => { setSelected(null); setCurated(event.target.value === 'true') }}>
        <option value="false">Awaiting curation</option>
        <option value="true">Curated</option>
      </select>
      <button className="button" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Refresh excerpts</button>
    </div>
    {query.isPending && <p role="status" className="text-sm text-slate-500">Loading imported excerpts…</p>}
    {query.isError && <p role="alert" className="text-sm text-amber-900">{query.error.message}</p>}
    {!query.isError && query.data && <>
      {query.data.length === 0 ? <p className="text-sm text-slate-500">No imported excerpts with this status. Import logs locally before reviewing them here.</p> : (
        <ul className="space-y-2">{query.data.map((log) => <li key={log.job_id}>
          <button type="button" className="w-full rounded-lg border border-slate-200 p-3 text-left text-xs leading-5 hover:border-indigo-300"
            aria-pressed={selected === log.job_id} onClick={() => setSelected(log.job_id)}>
            <span className="block break-all font-semibold text-slate-800">{log.repository} · #{log.github_run_id} · Attempt {log.run_attempt}</span>
            <span className="block break-all text-slate-600">{log.job_name} · Job {log.job_id}</span>
            <span className="text-slate-500">Updated <SourceTime value={log.updated_at} /></span>
          </button>
        </li>)}</ul>
      )}
      <p className="text-xs text-slate-500">Up to 20 most recently updated excerpts.</p>
      {selected !== null && <LogReview key={selected} jobId={selected} token={token} onUnauthorized={onUnauthorized} onChanged={onChanged} />}
    </>}
  </section>
}

function LogReview({ jobId, token, onUnauthorized, onChanged }: Props & { jobId: number }) {
  const client = useQueryClient()
  const [reviewedRevision, setReviewedRevision] = useState<string | null>(null)
  const query = useQuery({ queryKey: ['reviewer-log', jobId], queryFn: ({ signal }) => fetchPrivateLog(jobId, token, signal) })
  const mutation = useMutation({ mutationFn: (decision: { expected_revision: string; is_curated: boolean }) => setLogCuration(jobId, token, decision), retry: false })
  const data = query.isError ? undefined : query.data
  useEffect(() => {
    if (query.error instanceof ReviewerAccessError) onUnauthorized(query.error.message)
  }, [query.error, onUnauthorized])
  function decide() {
    if (!data || mutation.isPending || query.isFetching || (!data.is_curated && reviewedRevision !== data.revision)) return
    mutation.mutate({ expected_revision: data.revision, is_curated: !data.is_curated }, {
      onSuccess: (result) => {
        setReviewedRevision(null)
        client.setQueryData(['reviewer-log', jobId], result)
        void client.invalidateQueries({ queryKey: ['reviewer-logs'] })
        onChanged()
      },
      onError: (error) => {
        setReviewedRevision(null)
        if (error instanceof ReviewerAccessError) onUnauthorized(error.message)
        else {
          void client.resetQueries({ queryKey: ['reviewer-log', jobId], exact: true })
          void client.invalidateQueries({ queryKey: ['reviewer-logs'] })
        }
        onChanged()
      },
    })
  }
  return <section aria-label="Private log excerpt" className="space-y-4 border-t border-slate-200 pt-5" aria-busy={query.isFetching || mutation.isPending}>
    <h4 className="text-sm font-semibold text-slate-900">Review processed excerpt</h4>
    {query.isPending && <p role="status" className="text-sm text-slate-500">Loading private excerpt…</p>}
    {query.isError && <p role="alert" className="text-sm text-amber-900">{query.error.message}</p>}
    {mutation.isError && <p role="alert" className="text-sm text-amber-900">{mutation.error.message}</p>}
    {data && <>
      <p role="status" className="text-xs text-indigo-700">{data.is_curated ? 'Curated: available publicly and for investigations.' : 'Uncurated: hidden publicly and excluded from investigations.'}</p>
      <p className="text-xs leading-5 text-slate-600">{data.excerpt.total_source_lines} source lines. {data.excerpt.truncated ? 'Partial excerpt; some content was omitted.' : 'Complete excerpt.'} {data.excerpt.redactions_applied && 'Credential patterns were masked.'}</p>
      <ol aria-label="Private log source lines" className="space-y-1 overflow-hidden rounded-md bg-slate-950 p-3 font-mono text-xs leading-5 text-slate-100">
        {data.excerpt.lines.map((line) => <li key={line.source_line} className="flex gap-3">
          <span aria-label={`Source line ${line.source_line}`} className="w-10 shrink-0 text-right text-slate-400">{line.source_line}</span>
          <code className="min-w-0 whitespace-pre-wrap break-all">{line.text || '\u00a0'}</code>
        </li>)}
      </ol>
      {data.excerpt.lines.length === 0 && <p className="text-xs text-slate-500">The stored excerpt is empty.</p>}
      {!data.is_curated && <label className="flex items-start gap-2 text-xs leading-5 text-slate-700">
        <input type="checkbox" className="mt-1" checked={reviewedRevision === data.revision} disabled={mutation.isPending || query.isFetching}
          onChange={(event) => setReviewedRevision(event.target.checked ? data.revision : null)} />
        I reviewed these lines and they are safe to make public and use as investigation evidence.
      </label>}
      <p className="text-xs leading-5 text-slate-500">Withdrawal hides this excerpt and makes affected reports stale. Curation does not generate reports or call an AI provider.</p>
      <button className="button" type="button" onClick={decide}
        disabled={mutation.isPending || query.isFetching || (!data.is_curated && reviewedRevision !== data.revision)}>
        {mutation.isPending ? 'Saving curation…' : data.is_curated ? 'Withdraw curation' : 'Curate excerpt'}
      </button>
    </>}
    <button className="button" type="button" disabled={query.isFetching || mutation.isPending} onClick={() => { setReviewedRevision(null); void query.refetch() }}>Refresh private excerpt</button>
  </section>
}
