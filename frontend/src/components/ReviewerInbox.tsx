import { useCallback, useEffect, useRef, useState } from 'react'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'

import { fetchPrivateDraft, fetchPrivateDrafts, ReviewerAccessError } from '../api/reviewer'
import { InvestigationReportContent } from './InvestigationReportContent'
import { SourceTime } from './SourceTime'

export function ReviewerInbox({ onClose }: { onClose: () => void }) {
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [input, setInput] = useState('')
  const [token, setToken] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  // Never put credentials in query keys or private results in the public cache.
  const [client] = useState(() => new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 0, gcTime: 0 } } }))
  const lock = useCallback((message?: string) => {
    setToken(null)
    setInput('')
    setError(message || null)
    void client.cancelQueries()
    client.clear()
  }, [client])

  useEffect(() => {
    const dialog = dialogRef.current!
    dialog.showModal()
    return () => {
      dialog.close()
      void client.cancelQueries()
      client.clear()
    }
  }, [client])

  return (
    <dialog ref={dialogRef} className="run-details" aria-labelledby="reviewer-inbox-heading" onClose={(event) => {
      if (!event.currentTarget.open) onClose()
    }}>
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white p-5">
        <h2 id="reviewer-inbox-heading" className="font-semibold text-slate-900">Private draft inbox</h2>
        <div className="flex gap-2">
          {token && <button className="button" type="button" onClick={() => lock()}>Lock inbox</button>}
          <button className="button" type="button" onClick={() => dialogRef.current?.close()}>Close inbox</button>
        </div>
      </div>
      <div className="p-5 sm:p-7">
        {!token ? (
          <form className="space-y-4" onSubmit={(event) => {
            event.preventDefault()
            setError(null)
            setToken(input.trim())
            setInput('')
          }}>
            <p className="text-sm leading-6 text-slate-500">Enter your reviewer access token to view private drafts. Access lasts while this inbox is open.</p>
            {error && <p role="alert" className="text-sm text-amber-900">{error}</p>}
            <label htmlFor="reviewer-token" className="block text-xs font-semibold text-slate-700">Reviewer access token</label>
            <input id="reviewer-token" type="password" autoComplete="off" spellCheck={false} autoCapitalize="none"
              className="min-h-10 w-full rounded-lg border border-slate-300 px-3 text-sm" required minLength={43} maxLength={128}
              value={input} onChange={(event) => setInput(event.target.value)} />
            <button className="button" type="submit">Unlock inbox</button>
          </form>
        ) : (
          <QueryClientProvider client={client}>
            <DraftBrowser token={token} onUnauthorized={lock} />
          </QueryClientProvider>
        )}
        <p className="mt-6 text-xs leading-5 text-slate-500">Viewing drafts does not approve or publish them.</p>
      </div>
    </dialog>
  )
}

function DraftBrowser({ token, onUnauthorized }: { token: string; onUnauthorized: (message: string) => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const query = useQuery({ queryKey: ['reviewer-drafts'], queryFn: ({ signal }) => fetchPrivateDrafts(token, signal) })
  useEffect(() => {
    if (query.error instanceof ReviewerAccessError) onUnauthorized(query.error.message)
  }, [query.error, onUnauthorized])
  if (query.isPending) return <p role="status" className="text-sm text-slate-500">Loading private drafts…</p>
  if (query.isError) return <div role="alert" className="text-sm text-amber-900">
    <p>{query.error.message}</p>
    <button className="button mt-3" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Retry inbox</button>
  </div>
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h3 className="text-sm font-semibold text-slate-800">Pending drafts</h3>
        <button className="button" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh inbox</button>
      </div>
      {query.data.length === 0 ? <p className="text-sm text-slate-500">No drafts are awaiting review.</p> : (
        <ul className="space-y-2">
          {query.data.map((draft) => <li key={draft.id}>
            <button className="w-full rounded-lg border border-slate-200 p-3 text-left text-xs leading-5 hover:border-indigo-300"
              type="button" onClick={() => setSelected(draft.id)} aria-pressed={selected === draft.id}>
              <span className="block break-all font-semibold text-slate-800">{draft.repository} · #{draft.github_run_id} · Attempt {draft.run_attempt}</span>
              <span className="text-slate-500">Imported <SourceTime value={draft.created_at} /></span>
            </button>
          </li>)}
        </ul>
      )}
      <p className="text-xs text-slate-500">Up to 20 most recently imported pending drafts.</p>
      {selected && <DraftDetails key={selected} id={selected} token={token} onUnauthorized={onUnauthorized} />}
    </div>
  )
}

function DraftDetails({ id, token, onUnauthorized }: { id: string; token: string; onUnauthorized: (message: string) => void }) {
  const query = useQuery({ queryKey: ['reviewer-draft', id], queryFn: ({ signal }) => fetchPrivateDraft(id, token, signal) })
  useEffect(() => {
    if (query.error instanceof ReviewerAccessError) onUnauthorized(query.error.message)
  }, [query.error, onUnauthorized])
  const data = query.isError ? undefined : query.data
  return (
    <section aria-labelledby="private-draft-heading" className="space-y-4 border-t border-slate-200 pt-5" aria-busy={query.isFetching}>
      <h3 id="private-draft-heading" className="text-sm font-semibold text-slate-900">Draft details</h3>
      {query.isPending && <p role="status" className="text-sm text-slate-500">Loading private report…</p>}
      {query.isError && <p role="alert" className="text-sm text-amber-900">{query.error.message}</p>}
      {data && <>
        <p className="text-xs text-indigo-700">AI-generated draft · {data.status.replaceAll('_', ' ')}</p>
        {data.freshness !== 'current' ? <p role="status" className="text-xs leading-5 text-amber-900">This draft can no longer be verified. Its report is hidden.</p> : data.report && <>
          <p className="break-all text-xs text-slate-500">Model: {data.model}</p>
          <InvestigationReportContent report={data.report} generatedAt={data.generated_at} />
        </>}
      </>}
      <button className="button" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh private report</button>
    </section>
  )
}
