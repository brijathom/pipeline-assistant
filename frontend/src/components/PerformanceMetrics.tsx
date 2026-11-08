import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchPerformance } from '../api/metrics'
import type { PipelinePerformance } from '../api/metrics'
import { formatDuration } from '../format'
import { DurationTrend } from './DurationTrend'
import { SourceTime } from './SourceTime'

const number = new Intl.NumberFormat('en-GB')

export function PerformanceMetrics({ repository }: { repository?: string }) {
  const [days, setDays] = useState<PipelinePerformance['days']>(30)
  const query = useQuery({ queryKey: ['performance', { repository, days }],
    queryFn: ({ signal }) => fetchPerformance(signal, days, repository) })
  const data = query.data
  return <section className="mb-7 rounded-xl border border-slate-200 bg-white p-4 shadow-xs sm:p-5"
    aria-labelledby="performance-heading" aria-busy={query.isFetching}>
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h2 id="performance-heading" className="text-sm font-semibold text-slate-800">Job performance</h2>
        <p className="mt-1 break-all text-xs text-slate-500">{repository || 'All repositories'}</p></div>
      <div className="flex items-center gap-2">
        <label htmlFor="performance-days" className="text-xs text-slate-600">Window</label>
        <select id="performance-days" className="min-h-10 rounded-lg border border-slate-300 bg-white p-2 text-sm" value={days}
          onChange={(event) => setDays(Number(event.target.value) as PipelinePerformance['days'])}>
          <option value={7}>Last 7 days</option><option value={30}>Last 30 days</option><option value={90}>Last 90 days</option>
        </select>
      </div>
    </div>
    {query.isPending && <p role="status" className="mt-4 text-sm text-slate-500">Loading job performance…</p>}
    {query.isError && <div role="alert" className="mt-4 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
      <p>{data ? 'Could not refresh job performance. Showing previously loaded values.' : 'Job performance could not be loaded.'}</p>
      <button className="button mt-3" type="button" disabled={query.isFetching} onClick={() => void query.refetch()}>Retry performance</button>
    </div>}
    {data && <>
      <dl className="mt-5 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {[
          ['Median job duration', formatDuration(data.median_job_duration_seconds)],
          ['P95 job duration', formatDuration(data.p95_job_duration_seconds)],
          ['Timed jobs', number.format(data.timed_jobs)],
        ].map(([title, value]) => <div key={title}><dt className="text-xs text-slate-500">{title}</dt>
          <dd className="mt-2 text-2xl font-semibold tabular-nums text-slate-900">{value}</dd></div>)}
      </dl>
      <p className="mt-4 text-xs leading-5 text-slate-500">{number.format(data.timed_jobs)} of {number.format(data.total_jobs)} stored jobs have valid durations; {number.format(data.excluded_jobs)} excluded.</p>
      {data.timed_jobs > 0 && data.timed_jobs < 20 && <p className="mt-2 text-xs text-amber-800">Small sample: fewer than 20 timed jobs. Interpret p95 cautiously.</p>}
      {data.timed_jobs === 0 ? <p role="status" className="mt-4 text-sm text-slate-500">No completed jobs with valid timings in this window.</p> : <DurationTrend daily={data.daily} />}
      <p className="mt-4 text-xs leading-5 text-slate-500">Attempts created from <SourceTime value={data.start_at} /> to <SourceTime value={data.end_at} />. Today is partial. Days group jobs by attempt creation date in UTC.</p>
    </>}
    <p className="mt-3 text-xs leading-5 text-slate-500">Job execution time excludes queueing. Parallel job durations are separate observations. Missing or invalid timings are excluded. Imported data is a partial view of GitHub history.</p>
  </section>
}
