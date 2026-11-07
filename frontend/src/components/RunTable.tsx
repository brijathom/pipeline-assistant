import type { PipelineRun } from '../api/runs'

const createdDate = new Intl.DateTimeFormat('en-GB', {
  day: '2-digit',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})

const createdTime = new Intl.DateTimeFormat('en-GB', {
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
  timeZone: 'UTC',
})

function readable(value: string): string {
  const text = value.replaceAll('_', ' ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function ResultBadge({ conclusion }: { conclusion: string | null }) {
  if (!conclusion) {
    return <span className="text-slate-400" aria-label="No conclusion yet">—</span>
  }

  const tone = conclusion === 'success'
    ? 'badge-success'
    : conclusion === 'failure' || conclusion === 'timed_out'
      ? 'badge-failure'
      : 'badge-neutral'

  return (
    <span className={`badge ${tone}`}>
      <span aria-hidden="true">{conclusion === 'success' ? '✓' : tone === 'badge-failure' ? '×' : '−'}</span>
      {readable(conclusion)}
    </span>
  )
}

export function RunTable({ runs, onSelectRun }: { runs: PipelineRun[]; onSelectRun: (id: number) => void }) {
  return (
    <div className="overflow-x-auto">
      <table className="runs-table">
        <caption className="sr-only">Recent pipeline runs, newest first. Creation times are in UTC.</caption>
        <thead>
          <tr>
            <th scope="col">Workflow / repository</th>
            <th scope="col" className="hidden sm:table-cell">Run</th>
            <th scope="col" className="hidden sm:table-cell">Status</th>
            <th scope="col">Conclusion</th>
            <th scope="col" className="hidden sm:table-cell">Created <span className="font-normal text-slate-400">UTC</span></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => {
            const created = new Date(run.created_at)

            return (
              <tr key={run.id}>
                <td>
                  <button
                    className="block text-left font-semibold text-indigo-700 underline decoration-indigo-200 underline-offset-4 hover:decoration-indigo-700"
                    type="button"
                    aria-haspopup="dialog"
                    aria-label={`View details for ${run.workflow_name}, run ${run.github_run_id}, attempt ${run.run_attempt}`}
                    onClick={() => onSelectRun(run.id)}
                  >{run.workflow_name}</button>
                  <span className="mt-1 block text-xs text-slate-500">{run.repository}</span>
                  <div className="mt-2 space-y-1 text-[10px] text-slate-500 sm:hidden">
                    <span className="block">#{run.github_run_id} · Attempt {run.run_attempt} · {readable(run.status)}</span>
                    <time className="block" dateTime={run.created_at}>
                      {createdDate.format(created)} · {createdTime.format(created)} UTC
                    </time>
                  </div>
                </td>
                <td className="hidden sm:table-cell">
                  <span className="font-mono text-xs text-slate-700">#{run.github_run_id}</span>
                  <span className="mt-1 block text-xs text-slate-500">Attempt {run.run_attempt}</span>
                </td>
                <td className="hidden sm:table-cell">
                  <span className={`status ${run.status === 'in_progress' ? 'status-active' : ''}`}>
                    <span className="status-dot" aria-hidden="true" />
                    {readable(run.status)}
                  </span>
                </td>
                <td><ResultBadge conclusion={run.conclusion} /></td>
                <td className="hidden sm:table-cell">
                  <time dateTime={run.created_at}>
                    <span className="block text-slate-700">{createdDate.format(created)}</span>
                    <span className="mt-1 block text-xs text-slate-500">{createdTime.format(created)}</span>
                  </time>
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
