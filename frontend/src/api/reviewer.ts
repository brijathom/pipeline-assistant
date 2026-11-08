import type { InvestigationReport } from './investigations'

export class ReviewerAccessError extends Error {}

export interface PrivateDraft {
  id: string
  pipeline_run_id: number
  repository: string
  github_run_id: number
  run_attempt: number
  status: 'pending_review' | 'approved' | 'rejected'
  created_at: string
}

export interface PrivateDraftDetails extends PrivateDraft {
  freshness: 'current' | 'stale' | 'invalid'
  model: string | null
  generated_at: string | null
  report: InvestigationReport | null
  reviewed_at?: string | null
  reviewed_by?: string | null
  review_note?: string | null
}

export interface ReviewDecision {
  decision: 'approved' | 'rejected'
  note: string
}

async function privateRequest<T>(path: string, token: string, signal?: AbortSignal, body?: ReviewDecision): Promise<T> {
  const unconfirmed = 'The review could not be confirmed. Check its refreshed status before trying again.'
  let response: Response
  try {
    response = await fetch(`/api/reviewer/${path}`, {
      signal, cache: 'no-store', credentials: 'omit', method: body ? 'POST' : 'GET',
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new Error(body ? unconfirmed : 'We could not load or verify the private drafts. Try again.')
  }
  if (response.status === 401 || response.status === 403) throw new ReviewerAccessError('Access token was not accepted. Enter it again.')
  if (response.status === 503) throw new Error(body ? unconfirmed : 'Reviewer access is unavailable. Check the backend configuration.')
  if (response.status === 409) throw new Error('The draft changed or was already reviewed. Check its refreshed status before continuing.')
  if (response.status === 422) throw new Error('Enter a review note of 1–1000 characters.')
  if (!response.ok) throw new Error(body ? unconfirmed : 'We could not load or verify the private drafts. Try again.')
  try {
    return await response.json() as T
  } catch {
    throw new Error('The private response could not be verified. Try again.')
  }
}

export function fetchPrivateDrafts(token: string, signal: AbortSignal, status: PrivateDraft['status'] = 'pending_review'): Promise<PrivateDraft[]> {
  return privateRequest(`drafts?limit=20&status=${status}`, token, signal)
}

export function fetchPrivateDraft(id: string, token: string, signal: AbortSignal): Promise<PrivateDraftDetails> {
  return privateRequest(`drafts/${encodeURIComponent(id)}`, token, signal)
}

export function submitPrivateReview(id: string, token: string, decision: ReviewDecision): Promise<PrivateDraftDetails> {
  return privateRequest(`drafts/${encodeURIComponent(id)}/review`, token, undefined, decision)
}
