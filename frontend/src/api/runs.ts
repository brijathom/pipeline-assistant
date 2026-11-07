export interface PipelineRun {
  id: number
  repository: string
  github_run_id: number
  run_attempt: number
  workflow_name: string
  status: string
  conclusion: string | null
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export const RUN_LIMIT = 20

export async function fetchRuns(signal: AbortSignal, repository?: string): Promise<PipelineRun[]> {
  const params = new URLSearchParams({ limit: String(RUN_LIMIT) })
  if (repository) params.set('repository', repository)
  const response = await fetch(`/api/runs?${params}`, {
    signal,
    headers: { Accept: 'application/json' },
  })

  if (!response.ok) {
    throw new Error('Pipeline runs could not be loaded.')
  }

  return response.json() as Promise<PipelineRun[]>
}
