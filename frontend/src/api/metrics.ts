export interface PipelineMetrics {
  repository: string | null
  total_runs: number
  successful_runs: number
  failed_runs: number
  in_progress_runs: number
}

export interface JobDurationSummary {
  total_jobs: number
  timed_jobs: number
  excluded_jobs: number
  median_job_duration_seconds: number | null
  p95_job_duration_seconds: number | null
}

export interface JobDurationDay extends JobDurationSummary { date: string }

export interface PipelinePerformance extends JobDurationSummary {
  repository: string | null
  days: 7 | 30 | 90
  start_at: string
  end_at: string
  daily: JobDurationDay[]
}

export async function fetchPerformance(signal: AbortSignal, days: PipelinePerformance['days'], repository?: string): Promise<PipelinePerformance> {
  const params = new URLSearchParams({ days: String(days) })
  if (repository) params.set('repository', repository)
  const response = await fetch(`/api/metrics/performance?${params}`, { signal, headers: { Accept: 'application/json' } })
  if (!response.ok) throw new Error('Job performance metrics could not be loaded.')
  return response.json() as Promise<PipelinePerformance>
}

export async function fetchMetrics(signal: AbortSignal, repository?: string): Promise<PipelineMetrics> {
  const params = new URLSearchParams()
  if (repository) params.set('repository', repository)
  const query = params.size ? `?${params}` : ''
  const response = await fetch(`/api/metrics${query}`, {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (!response.ok) throw new Error('Reliability metrics could not be loaded.')
  return response.json() as Promise<PipelineMetrics>
}
