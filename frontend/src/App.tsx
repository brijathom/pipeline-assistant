import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'

import { fetchMetrics } from './api/metrics'
import { fetchRuns, RUN_LIMIT } from './api/runs'
import { MetricsCards } from './components/MetricsCards'
import { RunDetails } from './components/RunDetails'
import { RunTable } from './components/RunTable'
import { ReviewerInbox } from './components/ReviewerInbox'

export default function App() {
  const [repositoryInput, setRepositoryInput] = useState('')
  const [repository, setRepository] = useState<string | undefined>()
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null)
  const [reviewerOpen, setReviewerOpen] = useState(false)
  const runsQuery = useQuery({
    queryKey: ['runs', { limit: RUN_LIMIT, repository }],
    queryFn: ({ signal }) => fetchRuns(signal, repository),
  })
  const metricsQuery = useQuery({
    queryKey: ['metrics', { repository }],
    queryFn: ({ signal }) => fetchMetrics(signal, repository),
  })
  const isRefreshing = runsQuery.isFetching || metricsQuery.isFetching

  const runs = runsQuery.data
  const isDemo = runs && runs.length > 0 && runs.every((run) => run.repository.startsWith('demo/'))

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>

      <aside className="sidebar">
        <a className="brand" href="/" aria-label="Pipeline Assistant home">
          <img src="/favicon.svg" alt="" width="34" height="34" />
          <span>Pipeline<span className="block text-xs font-normal tracking-normal text-slate-500">Assistant</span></span>
        </a>
        <p className="sidebar-label">Workspace</p>
        <nav aria-label="Main navigation">
          <a className="nav-link" href="#main-content" aria-current="page">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
              <rect x="3" y="3" width="6" height="6" rx="1.5" />
              <rect x="15" y="15" width="6" height="6" rx="1.5" />
              <path d="M9 6h6a3 3 0 0 1 3 3v6M6 9v6a3 3 0 0 0 3 3h6" />
            </svg>
            Pipeline runs
          </a>
        </nav>
        <div className="sidebar-note">
          <span className="status-dot bg-emerald-600" aria-hidden="true" />
          Read-only workspace
        </div>
      </aside>

      <div className="min-w-0">
        <header className="topbar">
          <div className="flex items-center gap-3">
            <img className="md:hidden" src="/favicon.svg" width="28" height="28" alt="" />
            <span className="text-slate-500">Workspace</span>
            <span className="text-slate-300" aria-hidden="true">/</span>
            <span className="font-medium text-slate-700">Pipeline runs</span>
          </div>
          <span className="hidden text-xs text-slate-500 sm:block">Pipeline Assistant</span>
        </header>

        <main id="main-content" className="main-content" tabIndex={-1}>
          <div className="mb-8 flex flex-wrap items-end justify-between gap-5">
            <div>
              <p className="eyebrow">Workflow visibility</p>
              <h1 className="mt-2 text-3xl font-semibold tracking-tight text-slate-900 sm:text-4xl">Pipeline runs</h1>
              <p className="mt-3 text-sm leading-6 text-slate-500">A clear view of your recent workflow executions.</p>
            </div>
            <div className="flex flex-wrap gap-2">
            <button className="button" type="button" onClick={() => setReviewerOpen(true)}>Reviewer inbox</button>
            <button
              className="button"
              type="button"
              onClick={() => void Promise.all([runsQuery.refetch(), metricsQuery.refetch()])}
              disabled={isRefreshing}
            >
              <svg className={isRefreshing ? 'animate-spin' : ''} width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden="true">
                <path d="M20 7v5h-5M4 17v-5h5" />
                <path d="M6.1 7a7 7 0 0 1 11.5-1L20 9M4 15l2.4 3A7 7 0 0 0 17.9 17" />
              </svg>
              {isRefreshing ? 'Refreshing…' : 'Refresh'}
            </button>
            </div>
          </div>

          <form
            className="mb-6"
            onSubmit={(event) => {
              event.preventDefault()
              setRepository(repositoryInput.trim().toLowerCase() || undefined)
            }}
          >
            <label className="mb-2 block text-xs font-semibold text-slate-700" htmlFor="repository">Repository</label>
            <div className="flex flex-wrap gap-2">
              <input
                id="repository"
                className="min-h-10 w-full rounded-lg border border-slate-300 bg-white px-3 text-sm sm:w-80"
                value={repositoryInput}
                onChange={(event) => setRepositoryInput(event.target.value)}
                placeholder="All repositories"
                maxLength={140}
                pattern="[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.\-]{1,100}"
                title="Enter owner/repository, or leave empty for all repositories."
                aria-describedby="repository-help"
                autoCapitalize="none"
                autoCorrect="off"
                spellCheck={false}
              />
              <button className="button" type="submit">Apply filter</button>
              {repository && (
                <button className="button" type="button" onClick={() => {
                  setRepositoryInput('')
                  setRepository(undefined)
                }}>Clear filter</button>
              )}
            </div>
            <p id="repository-help" className="mt-2 text-xs leading-5 text-slate-500">Enter owner/repository to filter stored data, or leave empty to show all repositories.</p>
          </form>

          {isDemo && (
            <div className="demo-notice">
              <span className="demo-label">Demo data</span>
              <p>These are fictional workflow runs. No GitHub account is connected.</p>
            </div>
          )}

          <section className="mb-7" aria-labelledby="metrics-heading" aria-busy={metricsQuery.isFetching}>
            <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
              <h2 id="metrics-heading" className="text-sm font-semibold text-slate-800">Reliability overview</h2>
              <span className="break-all text-xs text-slate-500">{repository || 'All repositories'}</span>
            </div>
            {metricsQuery.isPending ? (
              <p role="status" className="rounded-xl border border-slate-200 bg-white p-5 text-sm text-slate-500">Loading reliability metrics…</p>
            ) : (
              <>
                {metricsQuery.isError && (
                  <div role="alert" className="mb-3 rounded-lg border border-amber-200 bg-amber-50 p-4 text-xs text-amber-900">
                    <p>{metricsQuery.data ? 'Couldn’t refresh metrics. Showing previously loaded values.' : 'We couldn’t load reliability metrics.'}</p>
                    <button className="button mt-3" type="button" onClick={() => void metricsQuery.refetch()} disabled={metricsQuery.isFetching}>Retry metrics</button>
                  </div>
                )}
                {metricsQuery.data && <MetricsCards metrics={metricsQuery.data} />}
              </>
            )}
            <p className="mt-3 text-xs leading-5 text-slate-500">Counts cover all stored attempts for this selection, not full GitHub history. Failed includes timed out; other outcomes remain in the total.</p>
          </section>

          <section className="runs-panel" aria-labelledby="recent-runs-heading" aria-busy={runsQuery.isFetching}>
            <div className="panel-heading">
              <div className="flex items-center gap-2.5">
                <h2 id="recent-runs-heading" className="text-sm font-semibold text-slate-800">Recent runs</h2>
                {runs && <span className="count-badge">{runs.length}</span>}
              </div>
              <span className="text-xs text-slate-500">Newest first</span>
            </div>

            {runsQuery.isPending ? (
              <div role="status" className="px-6 py-8">
                <p className="mb-5 text-sm text-slate-500">Loading pipeline runs…</p>
                <div className="space-y-4" aria-hidden="true">
                  {[0, 1, 2].map((row) => <div key={row} className="skeleton h-12 rounded-md" />)}
                </div>
              </div>
            ) : runsQuery.isError && !runs ? (
              <div className="empty-state" role="alert">
                <span className="state-symbol bg-rose-50 text-rose-600" aria-hidden="true">!</span>
                <h3>We couldn’t load pipeline runs</h3>
                <p>Try refreshing in a moment.</p>
                <button className="button mt-5" type="button" onClick={() => void runsQuery.refetch()} disabled={runsQuery.isFetching}>Try again</button>
              </div>
            ) : runs?.length === 0 ? (
              <div className="empty-state">
                <span className="state-symbol bg-slate-100 text-slate-500" aria-hidden="true">≡</span>
                <h3>No pipeline runs yet</h3>
                <p>Runs will appear here when data becomes available.</p>
              </div>
            ) : runs ? (
              <>
                {runsQuery.isError && <p className="refresh-warning" role="alert">Couldn’t refresh. Showing previously loaded runs.</p>}
                <RunTable runs={runs} onSelectRun={setSelectedRunId} />
              </>
            ) : null}

            <div className="panel-footer">
              <span>Up to {RUN_LIMIT} most recent runs</span>
              <span>Times in UTC</span>
            </div>
          </section>

        </main>
      </div>
      {selectedRunId !== null && <RunDetails key={selectedRunId} runId={selectedRunId} onClose={() => setSelectedRunId(null)} />}
      {reviewerOpen && <ReviewerInbox onClose={() => setReviewerOpen(false)} />}
    </div>
  )
}
