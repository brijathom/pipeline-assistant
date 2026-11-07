import { readable } from '../format'

export function ResultBadge({ conclusion }: { conclusion: string | null }) {
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
