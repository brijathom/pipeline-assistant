import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'

import { queueInvestigationTask, ReviewerAccessError } from '../api/reviewer'
import { fetchRuns } from '../api/runs'
import { useTaskEvents } from '../hooks/useTaskEvents'
import { SourceTime } from './SourceTime'

const failures: Record<string, string> = {
  evidence_changed: 'Evidence changed or lost curation. Review current evidence before submitting a new task.',
  invalid_snapshot: 'The queued snapshot or prompt version could not be verified.',
  generation_failed: 'Generation failed or the report was invalid. No draft was saved.',
  worker_interrupted: 'The worker was marked interrupted. Its model request was not automatically retried.',
}

export function InvestigationTasks({ token, onUnauthorized, onOpenDrafts }: {
  token: string; onUnauthorized: (message: string) => void; onOpenDrafts: () => void
}) {
  const [runId, setRunId] = useState('')
  const [requestId, setRequestId] = useState<string | null>(null)
  const runs = useQuery({ queryKey: ['reviewer-task-runs'], queryFn: ({ signal }) => fetchRuns(signal, undefined, 100) })
  const progress = useTaskEvents(token, onUnauthorized)
  const mutation = useMutation({ mutationFn: (request: { request_id: string; run_id: number }) => queueInvestigationTask(token, request), retry: false })
  const tasks = progress.tasks
  const active = tasks?.some((task) => task.pipeline_run_id === Number(runId) && (task.status === 'queued' || task.status === 'running'))
  return <section aria-label="Investigation tasks" className="space-y-5">
    <h3 className="text-sm font-semibold text-slate-900">Investigation tasks</h3>
    <p className="text-xs leading-5 text-slate-600">Queue one investigation of a run's current curated failed-job evidence. Processing uses Gemini quota and saves an unreviewed private draft. A separate worker processes queued tasks.</p>
    <form className="space-y-3" onSubmit={(event) => {
      event.preventDefault()
      if (!runId || mutation.isPending || active || progress.phase !== 'live') return
      const id = requestId || crypto.randomUUID()
      setRequestId(id)
      mutation.mutate({ request_id: id, run_id: Number(runId) }, {
        onSuccess: () => {
          progress.reconnect()
          setRequestId(null)
          setRunId('')
        },
        onError: (error) => {
          if (error instanceof ReviewerAccessError) onUnauthorized(error.message)
          else progress.reconnect()
        },
      })
    }}>
      <label htmlFor="task-run" className="block text-xs font-semibold text-slate-700">Run to investigate</label>
      <select id="task-run" className="min-h-10 w-full rounded-lg border border-slate-300 bg-white p-2 text-sm" required
        value={runId} disabled={mutation.isPending || requestId !== null || runs.isPending || runs.isError}
        onChange={(event) => setRunId(event.target.value)}>
        <option value="">Choose a stored run</option>
        {runs.data?.map((run) => <option key={run.id} value={run.id}>{run.repository} · #{run.github_run_id} · Attempt {run.run_attempt} · {run.conclusion || run.status}</option>)}
      </select>
      <p className="text-xs text-slate-500">Up to 100 recent stored runs. The server checks evidence before accepting a task.</p>
      {runs.isError && <p role="alert" className="text-sm text-amber-900">Run choices could not be loaded. Reopen this view to retry.</p>}
      {mutation.isError && <p role="alert" className="text-sm text-amber-900">{mutation.error.message}</p>}
      <div className="flex flex-wrap gap-2">
        <button className="button" type="submit" disabled={!runId || mutation.isPending || active || progress.phase !== 'live'}>
          {mutation.isPending ? 'Submitting task…' : requestId ? 'Retry same request' : 'Queue investigation'}
        </button>
        {requestId && !mutation.isPending && <button className="button" type="button" onClick={() => { setRequestId(null); setRunId(''); mutation.reset() }}>Clear submission</button>}
      </div>
      {active && <p role="status" className="text-xs text-slate-500">This run already has an active task.</p>}
      {requestId && !mutation.isPending && <p className="text-xs text-slate-500">Check the task list before retrying. Retrying this submission reuses its original request ID.</p>}
    </form>
    <button className="button" type="button" disabled={progress.phase === 'connecting'} onClick={progress.reconnect}>Refresh tasks</button>
    {progress.phase === 'connecting' && <p role="status" className="text-sm text-slate-500">Connecting to task progress…</p>}
    {progress.phase === 'live' && <p role="status" className="text-xs text-emerald-800">Live task updates connected.</p>}
    {progress.phase === 'reconnecting' && <p role="alert" className="text-sm text-amber-900">Task progress disconnected. Reconnecting; previous task details are hidden.</p>}
    {progress.phase === 'stopped' && <p role="alert" className="text-sm text-amber-900">Task progress is unavailable after repeated attempts. Refresh tasks to try again.</p>}
    {tasks?.length === 0 && <p className="text-sm text-slate-500">No investigation tasks yet.</p>}
    {tasks && <ul className="space-y-3">{tasks.map((task) => <li key={task.id} className="space-y-2 rounded-lg border border-slate-200 p-3 text-xs leading-5">
      <p className="break-all font-semibold text-slate-800">{task.repository} · #{task.github_run_id} · Attempt {task.run_attempt}</p>
      <p role="status" className="font-semibold text-indigo-700">Status: {task.status}</p>
      <p className="break-all text-slate-500">Task {task.id}</p>
      <p className="text-slate-500">Queued <SourceTime value={task.created_at} /></p>
      {task.error_code && <p className="text-amber-900">{failures[task.error_code] || 'Task failed. Check its status before taking further action.'}</p>}
      {task.dispatch_exhausted && <p role="alert" className="text-amber-900">Automatic delivery stopped after repeated failures. Operator recovery is required; this task will not start a new model attempt automatically.</p>}
      {task.status === 'succeeded' && task.draft_id && <button className="button" type="button" onClick={onOpenDrafts}>Review drafts</button>}
    </li>)}</ul>}
    <p className="text-xs leading-5 text-slate-500">Live updates show the 20 most recent tasks. Reconnecting restores current status. Failed and interrupted model requests are not automatically retried.</p>
  </section>
}
