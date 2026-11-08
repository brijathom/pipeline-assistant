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
}

async function privateGet<T>(path: string, token: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(`/api/reviewer/${path}`, {
    signal, cache: 'no-store', credentials: 'omit',
    headers: { Accept: 'application/json', Authorization: `Bearer ${token}` },
  })
  if (response.status === 401 || response.status === 403) throw new ReviewerAccessError('Access token was not accepted. Enter it again.')
  if (response.status === 503) throw new Error('Reviewer access is unavailable. Check the backend configuration.')
  if (!response.ok) throw new Error('We could not load or verify the private drafts. Try again.')
  try {
    return await response.json() as T
  } catch {
    throw new Error('The private response could not be verified. Try again.')
  }
}

export function fetchPrivateDrafts(token: string, signal: AbortSignal): Promise<PrivateDraft[]> {
  return privateGet('drafts?limit=20', token, signal)
}

export function fetchPrivateDraft(id: string, token: string, signal: AbortSignal): Promise<PrivateDraftDetails> {
  return privateGet(`drafts/${encodeURIComponent(id)}`, token, signal)
}
