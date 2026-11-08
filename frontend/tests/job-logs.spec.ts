import { expect, test } from '@playwright/test'

import type { JobLogExcerpt } from '../src/api/logs'
import type { PipelineRun } from '../src/api/runs'

const run: PipelineRun = {
  id: 701, repository: 'example/project', github_run_id: 1234, run_attempt: 1,
  workflow_name: 'Build and test', status: 'completed', conclusion: 'failure',
  created_at: '2026-11-07T12:00:00Z', started_at: null, completed_at: null,
}
const excerpt: JobLogExcerpt = {
  lines: [{ source_line: 17, text: 'TOKEN=[REDACTED]' },
    { source_line: 18, text: '<img src=x onerror="window.logExecuted=true">' },
    { source_line: 19, text: `FAILED ${'long-diagnostic-'.repeat(30)}` }],
  total_source_lines: 40, truncated: true, redactions_applied: true, source_kind: 'demo',
  updated_at: '2026-11-07T12:03:00Z',
}

test.describe('stored log excerpts', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/runs?*', (route) => route.fulfill({ json: [run] }))
    await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
      repository: null, total_runs: 1, successful_runs: 0, failed_runs: 1, in_progress_runs: 0,
    } }))
    await page.route('**/api/runs/701', (route) => route.fulfill({ json: run }))
    await page.route('**/api/runs/701/jobs', (route) => route.fulfill({ json: [{
      id: 801, github_job_id: 7001, name: 'Test backend', status: 'completed', conclusion: 'failure',
      started_at: null, completed_at: null, steps: [],
    }] }))
  })

  test('loads only on demand and renders numbered plain text on mobile', async ({ page }) => {
    let requests = 0
    await page.route('**/api/runs/701/jobs/801/log', (route) => {
      requests += 1
      return route.fulfill({ json: excerpt })
    })
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await page.getByRole('table').getByRole('button').click()
    const details = page.getByRole('dialog')
    const toggle = details.getByRole('button', { name: 'View log excerpt' })
    await expect(toggle).toBeVisible()
    expect(requests).toBe(0)
    await toggle.click()
    const log = details.getByRole('region', { name: 'Log excerpt', exact: true })
    await expect(log).toContainText('Fictional demo evidence.')
    await expect(log).toContainText('Partial excerpt; some content was omitted.')
    await expect(log.getByRole('listitem').locator('span')).toHaveText(['17', '18', '19'])
    await expect(log.locator('code').nth(1)).toHaveText(excerpt.lines[1].text)
    await expect(log.locator('img')).toHaveCount(0)
    expect(await page.evaluate(() => 'logExecuted' in window)).toBeFalsy()
    expect(await details.evaluate((dialog) => dialog.scrollWidth <= dialog.clientWidth)).toBeTruthy()
    await page.screenshot({ path: 'test-results/job-log-mobile.png' })
    await details.getByRole('button', { name: 'Hide log excerpt' }).click()
    await expect(log).toHaveCount(0)
  })

  test('shows loading and retries without hiding job metadata', async ({ page }) => {
    let release!: () => void
    let fail = true
    const gate = new Promise<void>((resolve) => { release = resolve })
    await page.route('**/api/runs/701/jobs/801/log', async (route) => {
      await gate
      await route.fulfill(fail ? { status: 503, json: {} } : { json: excerpt })
    })
    await page.goto('/')
    await page.getByRole('table').getByRole('button').click()
    await page.getByRole('button', { name: 'View log excerpt' }).click()
    const log = page.getByRole('region', { name: 'Log excerpt', exact: true })
    try {
      await expect(log.getByRole('status')).toHaveText('Loading log excerpt…')
      await expect(page.getByRole('heading', { name: 'Test backend', exact: true })).toBeVisible()
    } finally {
      release()
    }
    await expect(log.getByRole('alert')).toContainText('We couldn’t load the log excerpt.')
    fail = false
    await log.getByRole('button', { name: 'Retry log' }).click()
    await expect(log.getByRole('listitem')).toHaveCount(3)
  })

  test('preserves cached evidence on failure and clears withdrawn evidence', async ({ page }) => {
    let state: 'loaded' | 'failed' | 'withdrawn' | 'missing' = 'loaded'
    await page.route('**/api/runs/701/jobs/801/log', (route) => route.fulfill(
      state === 'failed' ? { status: 503, json: {} } : state === 'missing' ? { status: 404, json: {} }
        : { json: state === 'withdrawn' ? null : excerpt },
    ))
    await page.goto('/')
    await page.getByRole('table').getByRole('button').click()
    await page.getByRole('button', { name: 'View log excerpt' }).click()
    const log = page.getByRole('region', { name: 'Log excerpt', exact: true })
    await expect(log.getByRole('listitem')).toHaveCount(3)
    state = 'failed'
    await log.getByRole('button', { name: 'Refresh log' }).click()
    await expect(log.getByRole('alert')).toContainText('Showing previously loaded evidence.')
    await expect(log.getByRole('listitem')).toHaveCount(3)
    state = 'withdrawn'
    await log.getByRole('button', { name: 'Retry log' }).click()
    await expect(log).toContainText('No curated log excerpt is available')
    await expect(log.getByRole('listitem')).toHaveCount(0)
    state = 'missing'
    await log.getByRole('button', { name: 'Refresh log' }).click()
    await expect(log.getByRole('alert')).toContainText('This job is no longer available.')
    await expect(log.getByRole('button', { name: 'Retry log' })).toHaveCount(0)
  })

  test('distinguishes an empty stored log from no published excerpt', async ({ page }) => {
    await page.route('**/api/runs/701/jobs/801/log', (route) => route.fulfill({
      json: { ...excerpt, lines: [], total_source_lines: 0, truncated: false, redactions_applied: false },
    }))
    await page.goto('/')
    await page.getByRole('table').getByRole('button').click()
    await page.getByRole('button', { name: 'View log excerpt' }).click()
    await expect(page.getByRole('region', { name: 'Log excerpt', exact: true })).toContainText('The stored log is empty.')
  })
})

test('displays the seeded excerpt through the real API', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Repository', exact: true }).fill('demo/pipeline-assistant')
  await page.getByRole('button', { name: 'Apply filter' }).click()
  const failedRow = page.getByRole('table').locator('tbody tr').filter({ hasText: '#1002' })
  await failedRow.getByRole('button').click()
  const job = page.getByRole('article', { name: 'Demo: Test backend' })
  await job.getByRole('button', { name: 'View log excerpt' }).click()
  const log = job.getByRole('region', { name: 'Log excerpt', exact: true })
  await expect(log).toContainText('CI_TOKEN=[REDACTED]')
  await expect(log).toContainText('AssertionError: expected HTTP 200, received HTTP 503')
  await expect(log.getByRole('listitem').first().locator('span')).toHaveText('3')
  await expect(log).not.toContainText('demo-only-placeholder')
})
