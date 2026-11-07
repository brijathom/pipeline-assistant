export interface PipelineMetrics {
  repository: string | null
  total_runs: number
  successful_runs: number
  failed_runs: number
  in_progress_runs: number
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
