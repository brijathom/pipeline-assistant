import { useCallback, useEffect, useRef, useState } from 'react'
import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { fetchPrivateDraft, fetchPrivateDrafts, publishPrivateDraft, ReviewerAccessError, submitPrivateReview } from '../api/reviewer'
import type { PrivateDraft, ReviewDecision } from '../api/reviewer'
import { InvestigationReportContent } from './InvestigationReportContent'
import { SourceTime } from './SourceTime'

export function ReviewerInbox({ onClose }: { onClose: () => void }) {
  const publicClient = useQueryClient()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const [input, setInput] = useState('')
  const [session, setSession] = useState<{ token: string; client: QueryClient } | null>(null)
  const cacheRef = useRef<QueryClient | null>(null)
  const [error, setError] = useState<string | null>(null)
  const lock = useCallback((message?: string) => {
    setSession(null)
    setInput('')
    setError(message || null)
    void cacheRef.current?.cancelQueries()
    cacheRef.current?.clear()
    cacheRef.current = null
  }, [])

  useEffect(() => {
    const dialog = dialogRef.current!
    dialog.showModal()
    return () => {
      dialog.close()
      void cacheRef.current?.cancelQueries()
      cacheRef.current?.clear()
      cacheRef.current = null
    }
  }, [])

  return (
    <dialog ref={dialogRef} className="run-details" aria-labelledby="reviewer-inbox-heading" onClose={(event) => {
      if (!event.currentTarget.open) onClose()
    }}>
      <div className="sticky top-0 z-10 flex flex-wrap items-center justify-between gap-3 border-b border-slate-200 bg-white p-5">
        <h2 id="reviewer-inbox-heading" className="font-semibold text-slate-900">Private draft inbox</h2>
        <div className="flex gap-2">
          {session && <button className="button" type="button" onClick={() => lock()}>Lock inbox</button>}
          <button className="button" type="button" onClick={() => dialogRef.current?.close()}>Close inbox</button>
        </div>
      </div>
      <div className="p-5 sm:p-7">
        {!session ? (
          <form className="space-y-4" onSubmit={(event) => {
            event.preventDefault()
            setError(null)
            // Create a cache on explicit unlock. Mount-effect cleanup would
            // clear active queries during React Strict Mode's effect check.
            const client = new QueryClient({ defaultOptions: {
              queries: { retry: false, staleTime: 0, gcTime: 0 }, mutations: { retry: false, gcTime: 0 },
            } })
            cacheRef.current = client
            setSession({ token: input.trim(), client })
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
          <QueryClientProvider client={session.client}>
            <DraftBrowser token={session.token} onUnauthorized={lock}
              onPublished={() => { void publicClient.invalidateQueries({ queryKey: ['run-investigation'] }) }} />
          </QueryClientProvider>
        )}
        <p className="mt-6 text-xs leading-5 text-slate-500">Approval records a review decision. Publication is a separate action.</p>
      </div>
    </dialog>
  )
}

function DraftBrowser({ token, onUnauthorized, onPublished }: { token: string; onUnauthorized: (message: string) => void; onPublished: () => void }) {
  const [selected, setSelected] = useState<string | null>(null)
  const [status, setStatus] = useState<PrivateDraft['status']>('pending_review')
  const query = useQuery({ queryKey: ['reviewer-drafts', status], queryFn: ({ signal }) => fetchPrivateDrafts(token, signal, status) })
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
        <h3 className="text-sm font-semibold text-slate-800">{status === 'pending_review' ? 'Pending drafts' : status === 'approved' ? 'Approved drafts' : 'Rejected drafts'}</h3>
        <button className="button" type="button" onClick={() => void query.refetch()} disabled={query.isFetching}>Refresh inbox</button>
      </div>
      <div className="flex items-center gap-3">
        <label htmlFor="reviewer-draft-status" className="text-xs font-semibold text-slate-700">Draft status</label>
        <select id="reviewer-draft-status" className="rounded-lg border border-slate-300 bg-white p-2" value={status}
          onChange={(event) => { setSelected(null); setStatus(event.target.value as PrivateDraft['status']) }}>
          <option value="pending_review">Pending review</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
        </select>
      </div>
      {query.data.length === 0 ? <p className="text-sm text-slate-500">{status === 'pending_review' ? 'No drafts are awaiting review.' : `No ${status} drafts.`}</p> : (
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
      <p className="text-xs text-slate-500">Up to 20 most recently imported drafts with this status.</p>
      {selected && <DraftDetails key={selected} id={selected} token={token} onUnauthorized={onUnauthorized} onPublished={onPublished} />}
    </div>
  )
}

function DraftDetails({ id, token, onUnauthorized, onPublished }: { id: string; token: string; onUnauthorized: (message: string) => void; onPublished: () => void }) {
  const client = useQueryClient()
  const [note, setNote] = useState('')
  const query = useQuery({ queryKey: ['reviewer-draft', id], queryFn: ({ signal }) => fetchPrivateDraft(id, token, signal) })
  const review = useMutation({ mutationFn: (decision: ReviewDecision) => submitPrivateReview(id, token, decision), retry: false })
  const publication = useMutation({ mutationFn: () => publishPrivateDraft(id, token), retry: false })
  useEffect(() => {
    if (query.error instanceof ReviewerAccessError) onUnauthorized(query.error.message)
  }, [query.error, onUnauthorized])
  const data = query.isError ? undefined : query.data
  const saving = review.isPending || publication.isPending
  function refreshAfterError(error: Error) {
    if (error instanceof ReviewerAccessError) onUnauthorized(error.message)
    else {
      void client.resetQueries({ queryKey: ['reviewer-draft', id], exact: true })
      void client.invalidateQueries({ queryKey: ['reviewer-drafts'] })
    }
  }
  function publish() {
    if (!data || data.status !== 'approved' || data.freshness !== 'current' || data.is_published || saving || query.isFetching) return
    publication.mutate(undefined, {
      onSuccess: (result) => {
        client.setQueryData(['reviewer-draft', id], result)
        onPublished()
      },
      onError: (error) => {
        refreshAfterError(error)
        // A lost response may follow a successful commit. Refresh only public
        // reads; never copy private response data into the dashboard cache.
        onPublished()
      },
    })
  }
  function decide(decision: ReviewDecision['decision']) {
    if (!data || data.status !== 'pending_review' || !note.trim() || saving || query.isFetching) return
    review.mutate({ decision, note: note.trim() }, {
      // Per-call callbacks run only while this observer remains mounted.
      onSuccess: (result) => {
        setNote('')
        client.setQueryData(['reviewer-draft', id], result)
        void client.invalidateQueries({ queryKey: ['reviewer-drafts'] })
      },
      onError: refreshAfterError,
    })
  }
  return (
    <section aria-labelledby="private-draft-heading" className="space-y-4 border-t border-slate-200 pt-5" aria-busy={query.isFetching || saving}>
      <h3 id="private-draft-heading" className="text-sm font-semibold text-slate-900">Draft details</h3>
      {query.isPending && <p role="status" className="text-sm text-slate-500">Loading private report…</p>}
      {query.isError && <p role="alert" className="text-sm text-amber-900">{query.error.message}</p>}
      {review.isError && <p role="alert" className="text-sm text-amber-900">{review.error.message}</p>}
      {publication.isError && <p role="alert" className="text-sm text-amber-900">{publication.error.message}</p>}
      {review.isSuccess && !publication.isPending && !data?.is_published && <p role="status" className="text-sm text-emerald-800">Review recorded. The public report is unchanged.</p>}
      {data && <>
        <p className="text-xs text-indigo-700">AI-generated draft · {data.status.replaceAll('_', ' ')}</p>
        {data.freshness !== 'current' ? <p role="status" className="text-xs leading-5 text-amber-900">This draft can no longer be verified. Its report is hidden.</p> : data.report && <>
          <p className="break-all text-xs text-slate-500">Model: {data.model}</p>
          <InvestigationReportContent report={data.report} generatedAt={data.generated_at} />
        </>}
        {data.status === 'pending_review' ? (
          <form aria-label="Review decision" className="space-y-3 border-t border-slate-200 pt-4" onSubmit={(event) => event.preventDefault()}>
            <label htmlFor="review-note" className="block text-xs font-semibold text-slate-700">Review note</label>
            <textarea id="review-note" className="min-h-24 w-full rounded-lg border border-slate-300 p-3 text-sm"
              required maxLength={1000} value={note} onChange={(event) => setNote(event.target.value)} disabled={review.isPending} />
            <p className="text-xs leading-5 text-slate-500">Explain your decision. It is final and will be recorded as the authenticated operator.</p>
            <div className="flex flex-wrap gap-2">
              <button className="button" type="button" onClick={() => decide('approved')}
                disabled={!note.trim() || review.isPending || query.isFetching || data.freshness !== 'current'}>
                {review.isPending && review.variables.decision === 'approved' ? 'Saving approval…' : 'Approve draft'}
              </button>
              <button className="button" type="button" onClick={() => decide('rejected')}
                disabled={!note.trim() || review.isPending || query.isFetching}>
                {review.isPending && review.variables.decision === 'rejected' ? 'Saving rejection…' : 'Reject draft'}
              </button>
            </div>
          </form>
        ) : (
          <div className="space-y-2 text-xs leading-5 text-slate-600">
            <p>Reviewed by {data.reviewed_by || 'Unavailable'} · <SourceTime value={data.reviewed_at || null} /></p>
            <p className="whitespace-pre-wrap break-words">{data.review_note}</p>
          </div>
        )}
        {data.is_published ? <p role="status" className="text-sm text-emerald-800">This draft is the current public report.</p> : data.status === 'approved' && (
          <div className="space-y-3 border-t border-slate-200 pt-4">
            <p className="text-xs leading-5 text-slate-600">Publishing makes this report and its cited excerpts public, replacing the existing report for this run. Your review note stays private.</p>
            <button className="button" type="button" onClick={publish}
              disabled={saving || query.isFetching || data.freshness !== 'current'}>
              {publication.isPending ? 'Publishing…' : 'Publish approved draft'}
            </button>
          </div>
        )}
      </>}
      <button className="button" type="button" onClick={() => void query.refetch()} disabled={query.isFetching || saving}>Refresh private report</button>
    </section>
  )
}
