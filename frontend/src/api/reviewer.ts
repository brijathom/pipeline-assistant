import type { InvestigationReport } from './investigations'
import type { JobLogExcerpt } from './logs'

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
  is_published?: boolean
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

export interface PrivateLog {
  job_id: number
  pipeline_run_id: number
  repository: string
  github_run_id: number
  run_attempt: number
  job_name: string
  is_curated: boolean
  updated_at: string
}

export interface PrivateLogDetails extends PrivateLog {
  revision: string
  excerpt: JobLogExcerpt
}

interface LogCurationDecision { expected_revision: string; is_curated: boolean }

export interface InvestigationTask {
  id: string
  pipeline_run_id: number
  repository: string
  github_run_id: number
  run_attempt: number
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  model: string
  prompt_version: string
  created_at: string
  started_at: string | null
  completed_at: string | null
  draft_id: string | null
  error_code: 'evidence_changed' | 'invalid_snapshot' | 'generation_failed' | 'worker_interrupted' | null
  dispatch_exhausted: boolean
}

interface TaskRequest { request_id: string; run_id: number }

async function privateRequest<T>(path: string, token: string, signal?: AbortSignal, body?: ReviewDecision | LogCurationDecision | TaskRequest, action: 'review' | 'publication' | 'curation' | 'task submission' = 'review'): Promise<T> {
  const writing = !!body || action === 'publication'
  const unconfirmed = `The ${action} could not be confirmed. Check its refreshed status before trying again.`
  let response: Response
  try {
    response = await fetch(`/api/reviewer/${path}`, {
      signal, cache: 'no-store', credentials: 'omit', method: writing ? 'POST' : 'GET',
      headers: { Accept: 'application/json', Authorization: `Bearer ${token}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new Error(writing ? unconfirmed : 'We could not load or verify the private drafts. Try again.')
  }
  if (response.status === 401 || response.status === 403) throw new ReviewerAccessError('Access token was not accepted. Enter it again.')
  if (response.status === 503) throw new Error(writing ? unconfirmed : 'Reviewer access is unavailable. Check the backend configuration.')
  if (response.status === 409) throw new Error(action === 'task submission' ? 'Task could not be queued. Check for an active investigation and current curated failed-job evidence.' : action === 'curation' ? 'The excerpt changed. Review the refreshed content before changing curation.' : action === 'publication' ? 'Publication was blocked. Check approval and refreshed evidence before continuing.' : 'The draft changed or was already reviewed. Check its refreshed status before continuing.')
  if (response.status === 422) throw new Error(action === 'task submission' ? 'Choose a valid run before queueing an investigation.' : action === 'curation' ? 'Reload the excerpt and review it again.' : 'Enter a review note of 1–1000 characters.')
  if (!response.ok) throw new Error(writing ? unconfirmed : 'We could not load or verify the private drafts. Try again.')
  try {
    return await response.json() as T
  } catch {
    throw new Error(writing ? unconfirmed : 'The private response could not be verified. Try again.')
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

export function publishPrivateDraft(id: string, token: string): Promise<PrivateDraftDetails> {
  return privateRequest(`drafts/${encodeURIComponent(id)}/publish`, token, undefined, undefined, 'publication')
}

export function fetchPrivateLogs(token: string, signal: AbortSignal, curated: boolean): Promise<PrivateLog[]> {
  return privateRequest(`logs?limit=20&is_curated=${curated}`, token, signal)
}

export function fetchPrivateLog(jobId: number, token: string, signal: AbortSignal): Promise<PrivateLogDetails> {
  return privateRequest(`logs/${jobId}`, token, signal)
}

export function setLogCuration(jobId: number, token: string, decision: LogCurationDecision): Promise<PrivateLogDetails> {
  return privateRequest(`logs/${jobId}/curation`, token, undefined, decision, 'curation')
}

export function fetchInvestigationTasks(token: string, signal: AbortSignal): Promise<InvestigationTask[]> {
  return privateRequest('tasks?limit=20', token, signal)
}

export function queueInvestigationTask(token: string, request: TaskRequest): Promise<InvestigationTask> {
  return privateRequest('tasks', token, undefined, request, 'task submission')
}
