import { expect, test } from '@playwright/test'

import type { PipelineMetrics } from '../src/api/metrics'
import type { PipelineRun } from '../src/api/runs'

const sampleMetrics: PipelineMetrics = {
  repository: null, total_runs: 57, successful_runs: 43, failed_runs: 7, in_progress_runs: 2,
}

test('displays real API runs on desktop and mobile', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))

  const response = await page.request.get('/api/runs?limit=20')
  expect(response.ok()).toBeTruthy()
  const runs = await response.json() as PipelineRun[]
  expect(runs.length).toBeGreaterThan(0)
  const metricsResponse = await page.request.get('/api/metrics')
  expect(metricsResponse.ok()).toBeTruthy()
  const metrics = await metricsResponse.json() as PipelineMetrics
  expect(metrics.total_runs).toBeGreaterThanOrEqual(runs.length)

  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'Pipeline runs', exact: true })).toBeVisible()
  const rows = page.getByRole('table').locator('tbody tr')
  await expect(rows).toHaveCount(runs.length)
  for (const [index, run] of runs.entries()) {
    await expect(rows.nth(index)).toContainText(run.workflow_name)
    await expect(rows.nth(index)).toContainText(run.repository)
    await expect(rows.nth(index)).toContainText(`#${run.github_run_id}`)
  }
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()
  const overview = page.getByRole('region', { name: 'Reliability overview' })
  for (const [label, value] of [
    ['Total stored runs', metrics.total_runs], ['Successful', metrics.successful_runs],
    ['Failed', metrics.failed_runs], ['In progress', metrics.in_progress_runs],
  ] as const) {
    await expect(overview.getByText(label, { exact: true }).locator('..').locator('dd')).toHaveText(value.toLocaleString('en-GB'))
  }
  await page.screenshot({ path: 'test-results/pipeline-runs-desktop.png', fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'Pipeline runs', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Conclusion', exact: true })).toBeVisible()
  await rows.first().scrollIntoViewIfNeeded()
  await expect(rows.first().getByRole('cell').last()).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/pipeline-runs-mobile.png', fullPage: true })
  expect(errors).toEqual([])
})

test('shows an empty state when no runs exist', async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 0, successful_runs: 0, failed_runs: 0, in_progress_runs: 0,
  } }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
  await expect(page.getByRole('region', { name: 'Reliability overview' }).locator('dd')).toHaveText(['0', '0', '0', '0'])
})

test('shows a loading state while waiting for the API', async ({ page }) => {
  let release!: () => void
  const responseGate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/runs?*', async (route) => {
    await responseGate
    await route.fulfill({ json: [] })
  })
  await page.route(/\/api\/metrics(?:\?|$)/, async (route) => {
    await responseGate
    await route.fulfill({ json: sampleMetrics })
  })

  await page.goto('/')
  await expect(page.getByRole('region', { name: 'Recent runs' }).getByRole('status')).toContainText('Loading pipeline runs')
  await expect(page.getByRole('region', { name: 'Reliability overview' }).getByRole('status')).toContainText('Loading reliability metrics')
  release()
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
})

test('recovers from an API error when retried', async ({ page }) => {
  let fail = true
  await page.route('**/api/runs?*', (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: [] },
  ))

  await page.goto('/')
  await expect(page.getByRole('alert')).toContainText('We couldn’t load pipeline runs')
  fail = false
  await page.getByRole('button', { name: 'Try again', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('keeps previously loaded runs visible when refresh fails', async ({ page }) => {
  const response = await page.request.get('/api/runs?limit=20')
  expect(response.ok()).toBeTruthy()
  const runs = await response.json() as PipelineRun[]
  expect(runs.length).toBeGreaterThan(0)

  let fail = false
  await page.route('**/api/runs?*', (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: runs },
  ))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: sampleMetrics },
  ))
  await page.goto('/')
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(runs.length)
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()

  fail = true
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByRole('region', { name: 'Recent runs' }).getByRole('alert')).toContainText('Showing previously loaded runs')
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(runs.length)
  const overview = page.getByRole('region', { name: 'Reliability overview' })
  await expect(overview.getByRole('alert')).toContainText('Showing previously loaded values')
  await expect(overview.getByText('Total stored runs', { exact: true }).locator('..').locator('dd')).toHaveText('57')
})

test('filters metrics and runs together and isolates cached repositories', async ({ page }) => {
  const sampleRun: PipelineRun = {
    id: 1, repository: 'other/project', github_run_id: 1234, run_attempt: 1,
    workflow_name: 'Build', status: 'completed', conclusion: 'success',
    created_at: '2026-11-07T12:00:00Z', started_at: null, completed_at: null,
  }
  await page.route('**/api/runs?*', (route) => {
    const repository = new URL(route.request().url()).searchParams.get('repository')
    return route.fulfill({ json: [{ ...sampleRun, repository: repository || 'other/project' }] })
  })
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => {
    const repository = new URL(route.request().url()).searchParams.get('repository')
    return route.fulfill({ json: { ...sampleMetrics, repository, total_runs: repository ? 57 : 80 } })
  })
  await page.goto('/')
  const overview = page.getByRole('region', { name: 'Reliability overview' })
  const total = overview.getByText('Total stored runs', { exact: true }).locator('..').locator('dd')
  await expect(total).toHaveText('80')
  await expect(page.getByRole('table')).toContainText('other/project')
  await page.getByLabel('Repository', { exact: true }).fill('EXAMPLE/PROJECT')
  await page.getByRole('button', { name: 'Apply filter' }).click()
  await expect(overview.getByText('example/project', { exact: true })).toBeVisible()
  await expect(total).toHaveText('57')
  await expect(page.getByRole('table')).toContainText('example/project')
  await expect(page.getByRole('table')).not.toContainText('other/project')
  await page.getByRole('button', { name: 'Clear filter' }).click()
  await expect(total).toHaveText('80')
  await expect(page.getByRole('table')).toContainText('other/project')
})

test('retries unavailable metrics independently of the run list', async ({ page }) => {
  let fail = true
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: sampleMetrics },
  ))
  await page.goto('/')
  const overview = page.getByRole('region', { name: 'Reliability overview' })
  await expect(overview.getByRole('alert')).toContainText('We couldn’t load reliability metrics')
  await expect(overview.locator('dd')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
  fail = false
  await overview.getByRole('button', { name: 'Retry metrics' }).click()
  await expect(overview.getByRole('alert')).toHaveCount(0)
  await expect(overview.getByText('Total stored runs', { exact: true }).locator('..').locator('dd')).toHaveText('57')
})
