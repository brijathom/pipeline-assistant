import type { PipelineMetrics } from '../api/metrics'

const number = new Intl.NumberFormat('en-GB')

export function MetricsCards({ metrics }: { metrics: PipelineMetrics }) {
  const cards = [
    { label: 'Total stored runs', value: metrics.total_runs, tone: 'text-slate-900' },
    { label: 'Successful', value: metrics.successful_runs, tone: 'text-emerald-700' },
    { label: 'Failed', value: metrics.failed_runs, tone: 'text-rose-700' },
    { label: 'In progress', value: metrics.in_progress_runs, tone: 'text-indigo-700' },
  ]

  return (
    <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {cards.map(({ label, value, tone }) => (
        <div key={label} className="rounded-xl border border-slate-200 bg-white p-4 shadow-xs sm:p-5">
          <dt className="text-xs font-medium text-slate-500">{label}</dt>
          <dd className={`mt-3 text-3xl font-semibold tracking-tight tabular-nums ${tone}`}>{number.format(value)}</dd>
        </div>
      ))}
    </dl>
  )
}
