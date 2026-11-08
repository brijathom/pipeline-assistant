import { useId } from 'react'
import type { JobDurationDay } from '../api/metrics'
import { formatDuration } from '../format'

const dayLabel = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', timeZone: 'UTC' })
const label = (date: string) => dayLabel.format(new Date(`${date}T00:00:00Z`))

export function DurationTrend({ daily }: { daily: JobDurationDay[] }) {
  const titleId = useId()
  const descriptionId = useId()
  const ceiling = Math.max(1, ...daily.map((day) => day.p95_job_duration_seconds ?? 0))
  const left = 60, top = 15, width = 680, height = 145
  const step = width / daily.length
  const y = (seconds: number) => top + height - (seconds / ceiling) * height
  return <div className="mt-5">
    <div className="mb-2 flex flex-wrap gap-4 text-xs text-slate-600">
      <span><span className="mr-1 inline-block h-2 w-3 rounded-sm bg-indigo-500" aria-hidden="true" />Median job duration</span>
      <span><span className="mr-1 inline-block h-2 w-2 rounded-full bg-amber-600" aria-hidden="true" />P95 job duration</span>
    </div>
    <div className="relative pl-14">
      <div className="absolute left-0 top-[15px] h-[145px] w-12 text-right text-[11px] text-slate-500" aria-hidden="true">
        {[0, 0.5, 1].map((fraction) => <span key={fraction} className="absolute right-0 -translate-y-1/2" style={{ top: `${(1 - fraction) * 100}%` }}>{formatDuration(ceiling * fraction)}</span>)}
      </div>
    <svg viewBox="60 0 700 170" className="h-[170px] w-full" preserveAspectRatio="none" role="img" aria-labelledby={`${titleId} ${descriptionId}`}>
      <title id={titleId}>Daily job duration trend</title>
      <desc id={descriptionId}>Median bars and p95 markers for jobs grouped by attempt creation day in UTC. Days without valid timings have gaps. Exact values and sample sizes follow in Daily values.</desc>
      {[0, 0.5, 1].map((fraction) => <g key={fraction}>
        <line x1={left} x2={left + width} y1={y(ceiling * fraction)} y2={y(ceiling * fraction)} stroke="#e2e8f0" />
      </g>)}
      {daily.map((day, index) => {
        if (day.median_job_duration_seconds === null || day.p95_job_duration_seconds === null) return null
        const x = left + (index + 0.5) * step
        return <g key={day.date}>
          <title>{`${day.date} UTC: median ${formatDuration(day.median_job_duration_seconds)}, p95 ${formatDuration(day.p95_job_duration_seconds)}, ${day.timed_jobs} timed jobs`}</title>
          <rect x={x - step * 0.3} y={Math.min(y(day.median_job_duration_seconds), top + height - 1)} width={step * 0.6}
            height={Math.max(1, height * day.median_job_duration_seconds / ceiling)} fill="#6366f1" rx="1" />
          <circle cx={x} cy={y(day.p95_job_duration_seconds)} r="2.5" fill="#b45309" />
        </g>
      })}
    </svg>
      <div className="flex justify-between gap-2 text-[11px] text-slate-500"><span>{label(daily[0].date)}</span><span>{label(daily[daily.length - 1].date)} UTC</span></div>
    </div>
    <details className="mt-3 text-xs text-slate-600">
      <summary className="cursor-pointer font-medium text-indigo-700">Daily values</summary>
      <div className="mt-3 max-h-72 overflow-auto rounded-lg border border-slate-200">
        <table className="w-full text-left tabular-nums">
          <caption className="sr-only">Daily job duration values by attempt creation date in UTC</caption>
          <thead><tr className="border-b border-slate-200 bg-slate-50">
            {['Date UTC', 'Median', 'P95', 'Timed', 'Excluded'].map((heading) => <th key={heading} scope="col" className="px-2 py-2 font-medium">{heading}</th>)}
          </tr></thead>
          <tbody>{[...daily].reverse().map((day) => <tr key={day.date} className="border-b border-slate-100">
            <th scope="row" className="whitespace-nowrap px-2 py-2 font-normal">{day.date}</th>
            <td className="px-2 py-2">{formatDuration(day.median_job_duration_seconds)}</td>
            <td className="px-2 py-2">{formatDuration(day.p95_job_duration_seconds)}</td>
            <td className="px-2 py-2">{day.timed_jobs}</td><td className="px-2 py-2">{day.excluded_jobs}</td>
          </tr>)}</tbody>
        </table>
      </div>
    </details>
  </div>
}
