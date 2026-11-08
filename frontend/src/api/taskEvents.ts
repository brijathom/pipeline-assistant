import type { InvestigationTask } from './reviewer'
import { ReviewerAccessError } from './reviewer'

const MAX_FRAME_CHARS = 128 * 1024
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const taskKeys = new Set(['id', 'pipeline_run_id', 'repository', 'github_run_id', 'run_attempt', 'status', 'model',
  'prompt_version', 'created_at', 'started_at', 'completed_at', 'draft_id', 'error_code', 'dispatch_exhausted'])

function taskSnapshot(value: unknown): InvestigationTask[] {
  const text = (value: unknown, limit: number) => typeof value === 'string' && value.length > 0 && value.length <= limit
  const nullableDate = (value: unknown) => value === null || (text(value, 64) && Number.isFinite(Date.parse(value as string)))
  const positive = (value: unknown) => typeof value === 'number' && Number.isSafeInteger(value) && value > 0
  if (!Array.isArray(value) || value.length > 20 || !value.every((task) => task && typeof task === 'object'
    && Object.keys(task).every((key) => taskKeys.has(key))
    && typeof task.id === 'string' && UUID.test(task.id)
    && positive(task.pipeline_run_id) && positive(task.github_run_id) && positive(task.run_attempt)
    && text(task.repository, 200) && text(task.model, 100) && text(task.prompt_version, 100)
    && ['queued', 'running', 'succeeded', 'failed'].includes(task.status)
    && text(task.created_at, 64) && Number.isFinite(Date.parse(task.created_at))
    && nullableDate(task.started_at) && nullableDate(task.completed_at)
    && (task.draft_id === null || (typeof task.draft_id === 'string' && UUID.test(task.draft_id)))
    && (task.error_code === null || ['evidence_changed', 'invalid_snapshot', 'generation_failed', 'worker_interrupted'].includes(task.error_code))
    && typeof task.dispatch_exhausted === 'boolean')
    || new Set(value.map((task) => task.id)).size !== value.length) {
    throw new Error('Task progress could not be verified.')
  }
  return value as InvestigationTask[]
}

/** Fetch-based SSE keeps the bearer credential out of URLs and browser storage. */
export async function readTaskEvents(token: string, signal: AbortSignal, onSnapshot: (tasks: InvestigationTask[]) => void): Promise<void> {
  const controller = new AbortController()
  const combined = AbortSignal.any([signal, controller.signal])
  let idleTimer: ReturnType<typeof setTimeout>
  const resetIdle = () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => controller.abort(), 15_000) }
  const lifetime = setTimeout(() => controller.abort(), 60_000)
  let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
  resetIdle()
  try {
    const response = await fetch('/api/reviewer/tasks/events', {
      signal: combined, cache: 'no-store', credentials: 'omit', redirect: 'error',
      headers: { Accept: 'text/event-stream', Authorization: `Bearer ${token}` },
    })
    if (response.status === 401 || response.status === 403) throw new ReviewerAccessError('Access token was not accepted. Enter it again.')
    if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim() !== 'text/event-stream' || !response.body) {
      throw new Error('Task progress is unavailable.')
    }
    reader = response.body.getReader()
    const decoder = new TextDecoder('utf-8', { fatal: true })
    let line = '', event = '', data: string[] = [], size = 0, previousCR = false, receivedSnapshot = false
    const finishLine = (): boolean => {
      if (line === '') {
        const kind = event
        const body = data.join('\n')
        event = ''; data = []; size = 0
        if (!kind && !body) return false // Comments/heartbeats.
        if (kind === 'tasks') {
          const tasks = taskSnapshot(JSON.parse(body))
          if (!combined.aborted) onSnapshot(tasks)
          receivedSnapshot = true
        } else if (kind === 'rotate' && receivedSnapshot) {
          return true // Expected bounded-server rotation; resynchronize on a new request.
        } else {
          throw new Error('Task progress could not be verified.')
        }
      } else if (!line.startsWith(':')) {
        const colon = line.indexOf(':')
        const field = colon < 0 ? line : line.slice(0, colon)
        const value = colon < 0 ? '' : line.slice(colon + 1).replace(/^ /, '')
        if (field === 'event') event = value
        if (field === 'data') data.push(value)
        // IDs describe snapshot versions, not a historical event cursor.
      }
      line = ''
      return false
    }
    while (!combined.aborted) {
      const chunk = await reader.read()
      if (chunk.done) throw new Error('Task progress disconnected.') // Never dispatch an incomplete final event.
      resetIdle()
      for (const char of decoder.decode(chunk.value, { stream: true })) {
        if (char === '\n' && previousCR) { previousCR = false; continue }
        previousCR = char === '\r'
        size += 1
        if (size > MAX_FRAME_CHARS) throw new Error('Task progress exceeded its size limit.')
        if (char === '\n' || char === '\r') {
          if (finishLine()) return
        } else line += char
      }
    }
    throw new Error('Task progress disconnected.')
  } finally {
    clearTimeout(idleTimer!)
    clearTimeout(lifetime)
    controller.abort()
    await reader?.cancel().catch(() => undefined)
    reader?.releaseLock()
  }
}
