import { expect, test } from '@playwright/test'
import type { PipelinePerformance } from '../src/api/metrics'

function performance(days: 7 | 30 | 90 = 30, repository: string | null = null): PipelinePerformance {
  const end = new Date('2026-11-08T12:00:00Z')
  const start = new Date('2026-11-08T00:00:00Z')
  start.setUTCDate(start.getUTCDate() - days + 1)
  const daily = Array.from({ length: days }, (_, index) => {
    const date = new Date(start)
    date.setUTCDate(date.getUTCDate() + index)
    const hasData = index === days - 2
    return { date: date.toISOString().slice(0, 10), total_jobs: hasData ? 4 : 0, timed_jobs: hasData ? 3 : 0,
      excluded_jobs: hasData ? 1 : 0, median_job_duration_seconds: hasData ? 120 : null, p95_job_duration_seconds: hasData ? 300 : null }
  })
  return { repository, days, start_at: start.toISOString(), end_at: end.toISOString(), total_jobs: 4, timed_jobs: 3,
    excluded_jobs: 1, median_job_duration_seconds: 120, p95_job_duration_seconds: 300, daily }
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 0, successful_runs: 0, failed_runs: 0, in_progress_runs: 0,
  } }))
})

test('shows durations, coverage, small-sample warning and accessible daily values on desktop and mobile', async ({ page }) => {
  await page.route('**/api/metrics/performance?*', (route) => route.fulfill({ json: performance() }))
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Job performance', exact: true })
  await expect(panel.getByText('Median job duration', { exact: true }).first().locator('..').locator('dd')).toHaveText('2m 0s')
  await expect(panel.getByText('P95 job duration', { exact: true }).first().locator('..').locator('dd')).toHaveText('5m 0s')
  await expect(panel).toContainText('3 of 4 stored jobs have valid durations; 1 excluded')
  await expect(panel).toContainText('Small sample')
  await expect(panel.getByRole('img', { name: /Daily job duration trend/ })).toBeVisible()
  await panel.getByText('Daily values', { exact: true }).click()
  const table = panel.getByRole('table')
  await expect(table.locator('tbody tr')).toHaveCount(30)
  await expect(table.getByRole('row').filter({ hasText: '2026-11-07' })).toContainText('2m 0s')
  await expect(table.getByRole('row').filter({ hasText: '2026-11-08' })).toContainText('Unavailable')
  await page.screenshot({ path: 'test-results/performance-desktop.png', fullPage: true })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await expect(panel.getByLabel('Window', { exact: true })).toBeVisible()
  await page.screenshot({ path: 'test-results/performance-mobile.png', fullPage: true })
})

test('isolates repository and window caches and includes performance in the main refresh', async ({ page }) => {
  const requests: string[] = []
  await page.route('**/api/metrics/performance?*', (route) => {
    const url = new URL(route.request().url())
    const days = Number(url.searchParams.get('days')) as 7 | 30 | 90
    const repository = url.searchParams.get('repository')
    requests.push(`${repository || 'all'}:${days}`)
    return route.fulfill({ json: { ...performance(days, repository), median_job_duration_seconds: repository ? 60 : 120 } })
  })
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Job performance', exact: true })
  const median = panel.getByText('Median job duration', { exact: true }).first().locator('..').locator('dd')
  await expect(median).toHaveText('2m 0s')
  await panel.getByLabel('Window', { exact: true }).selectOption('7')
  await expect.poll(() => requests.includes('all:7')).toBeTruthy()
  await page.getByLabel('Repository', { exact: true }).fill('EXAMPLE/PROJECT')
  await page.getByRole('button', { name: 'Apply filter', exact: true }).click()
  await expect(median).toHaveText('1m 0s')
  await expect.poll(() => requests.includes('example/project:7')).toBeTruthy()
  await page.getByRole('button', { name: 'Clear filter', exact: true }).click()
  await expect(median).toHaveText('2m 0s')
  const before = requests.length
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect.poll(() => requests.length).toBeGreaterThan(before)
  expect(requests.at(-1)).toBe('all:7')
})

test('empty and genuine zero-duration samples have different displays', async ({ page }) => {
  let zero = false
  await page.route('**/api/metrics/performance?*', (route) => {
    const data = performance()
    return route.fulfill({ json: { ...data, total_jobs: zero ? 1 : 0, timed_jobs: zero ? 1 : 0, excluded_jobs: 0,
      median_job_duration_seconds: zero ? 0 : null, p95_job_duration_seconds: zero ? 0 : null,
      daily: data.daily.map((day, index) => ({ ...day, total_jobs: zero && index === 29 ? 1 : 0, timed_jobs: zero && index === 29 ? 1 : 0,
        excluded_jobs: 0, median_job_duration_seconds: zero && index === 29 ? 0 : null, p95_job_duration_seconds: zero && index === 29 ? 0 : null })) } })
  })
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Job performance', exact: true })
  const median = panel.getByText('Median job duration', { exact: true }).first().locator('..').locator('dd')
  await expect(median).toHaveText('Unavailable')
  await expect(panel).toContainText('No completed jobs with valid timings')
  await expect(panel.getByRole('img')).toHaveCount(0)
  zero = true
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(median).toHaveText('0s')
  await expect(panel.getByRole('img')).toBeVisible()
})

test('retries independently and labels stale data after a failed refresh', async ({ page }) => {
  let fail = true
  await page.route('**/api/metrics/performance?*', (route) => route.fulfill(fail ? { status: 503, json: {} } : { json: performance() }))
  await page.goto('/')
  const panel = page.getByRole('region', { name: 'Job performance', exact: true })
  await expect(panel.getByRole('alert')).toContainText('Job performance could not be loaded')
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
  fail = false
  await panel.getByRole('button', { name: 'Retry performance', exact: true }).click()
  await expect(panel).toContainText('3 of 4 stored jobs')
  fail = true
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(panel.getByRole('alert')).toContainText('Showing previously loaded values')
  await expect(panel).toContainText('3 of 4 stored jobs')
})

test('shows a loading state and rejects a late response from another repository', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/metrics/performance?*', async (route) => {
    const repo = new URL(route.request().url()).searchParams.get('repository')
    if (!repo) { await gate; return route.fulfill({ json: performance() }).catch(() => {}) }
    return route.fulfill({ json: { ...performance(30, repo), median_job_duration_seconds: 600 } })
  })
  try {
    await page.goto('/')
    const panel = page.getByRole('region', { name: 'Job performance', exact: true })
    await expect(panel.getByRole('status')).toContainText('Loading job performance')
    await page.getByLabel('Repository', { exact: true }).fill('example/project')
    await page.getByRole('button', { name: 'Apply filter', exact: true }).click()
    const median = panel.getByText('Median job duration', { exact: true }).first().locator('..').locator('dd')
    await expect(median).toHaveText('10m 0s')
    release()
    await expect(median).toHaveText('10m 0s')
  } finally { release() }
})
