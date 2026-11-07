import { RunNotFoundError } from './runs'

export interface PipelineJobStep {
  id: number
  step_number: number
  name: string
  status: string
  conclusion: string | null
  started_at: string | null
  completed_at: string | null
}

export interface PipelineJob {
  id: number
  github_job_id: number
  name: string
  status: string
  conclusion: string | null
  started_at: string | null
  completed_at: string | null
  steps: PipelineJobStep[]
}

export async function fetchRunJobs(runId: number, signal: AbortSignal): Promise<PipelineJob[]> {
  const response = await fetch(`/api/runs/${runId}/jobs`, {
    signal,
    headers: { Accept: 'application/json' },
  })
  if (response.status === 404) throw new RunNotFoundError('Pipeline run not found.')
  if (!response.ok) throw new Error('Job evidence could not be loaded.')
  return response.json() as Promise<PipelineJob[]>
}
