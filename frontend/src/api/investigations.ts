import { RunNotFoundError } from './runs'

export interface InvestigationReport {
  schema_version: 1
  summary: string
  findings: {
    title: string
    observation: string
    recommendation: string
    citation: { job_id: number; job_name: string; source_line: number; quote: string }
  }[]
  limitations: string[]
}

export interface RunInvestigation {
  status: 'ready' | 'stale' | 'not_generated'
  generation_method: 'rules-v1' | null
  generated_at: string | null
  evidence_fingerprint: string | null
  report: InvestigationReport | null
}

export async function fetchInvestigation(runId: number, signal: AbortSignal): Promise<RunInvestigation> {
  const response = await fetch(`/api/runs/${runId}/investigation`, {
    signal, cache: 'no-store', headers: { Accept: 'application/json' },
  })
  if (response.status === 404) throw new RunNotFoundError('Pipeline run not found.')
  if (!response.ok) throw new Error('Investigation could not be loaded.')
  return response.json() as Promise<RunInvestigation>
}
