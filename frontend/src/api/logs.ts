export interface JobLogExcerpt {
  lines: { source_line: number; text: string }[]
  total_source_lines: number
  truncated: boolean
  redactions_applied: boolean
  source_kind: 'github' | 'demo'
  updated_at: string
}

export class JobNotFoundError extends Error {}

export async function fetchJobLog(runId: number, jobId: number, signal: AbortSignal): Promise<JobLogExcerpt | null> {
  const response = await fetch(`/api/runs/${runId}/jobs/${jobId}/log`, {
    signal, cache: 'no-store', headers: { Accept: 'application/json' },
  })
  if (response.status === 404) throw new JobNotFoundError('Job is no longer available for this run.')
  if (!response.ok) throw new Error('Log excerpt could not be loaded.')
  return response.json() as Promise<JobLogExcerpt | null>
}
