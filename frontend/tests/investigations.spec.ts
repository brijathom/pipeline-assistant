import { expect, test } from '@playwright/test'

import type { RunInvestigation } from '../src/api/investigations'
import type { PipelineRun } from '../src/api/runs'

const run: PipelineRun = {
  id: 701, repository: 'example/project', github_run_id: 1234, run_attempt: 1,
  workflow_name: 'Build and test', status: 'completed', conclusion: 'failure',
  created_at: '2026-11-07T12:00:00Z', started_at: null, completed_at: null,
}
const unavailable: RunInvestigation = {
  status: 'not_generated', generation_method: null, generated_at: null, evidence_fingerprint: null, report: null,
}
const ready: RunInvestigation = {
  status: 'ready', generation_method: 'rules-v1', generated_at: '2026-11-07T12:03:00Z', evidence_fingerprint: 'a'.repeat(64),
  report: {
    schema_version: 1, summary: 'Found one supported failure diagnostic.',
    findings: [{
      title: 'HTTP status assertion failed', observation: 'A test expected HTTP 200 but received HTTP 503.',
      recommendation: 'Reproduce the failing test before changing retries.',
      citation: { job_id: 801, job_name: 'Backend tests', source_line: 6,
        quote: 'AssertionError: expected HTTP 200, received HTTP 503 <script>window.reportExecuted=true</script>' },
    }],
    limitations: ['The underlying root cause is not established.'],
  },
}

test.describe('stored investigations', () => {
  test.beforeEach(async ({ page }) => {
    await page.route('**/api/runs?*', (route) => route.fulfill({ json: [run, { ...run, id: 702, run_attempt: 2 }] }))
    await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
      repository: null, total_runs: 2, successful_runs: 0, failed_runs: 2, in_progress_runs: 0,
    } }))
    await page.route(/\/api\/runs\/70[12]$/, (route) => route.fulfill({
      json: route.request().url().endsWith('/701') ? run : { ...run, id: 702, run_attempt: 2 },
    }))
    await page.route('**/api/runs/*/jobs', (route) => route.fulfill({ json: [] }))
    await page.route('**/api/runs/*/investigation', (route) => route.fulfill({ json: unavailable }))
  })

  test('renders recommendations and exact citations as plain text on mobile', async ({ page }) => {
    await page.route('**/api/runs/701/investigation', (route) => route.fulfill({ json: ready }))
    await page.setViewportSize({ width: 390, height: 844 })
    await page.goto('/')
    await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
    const region = page.getByRole('region', { name: 'Investigation', exact: true })
    await expect(region).toContainText('Rule-based evidence summary · No AI model was called.')
    await expect(region).toContainText('Backend tests · Source line 6')
    await expect(region.locator('blockquote')).toHaveText(ready.report!.findings[0].citation.quote)
    await expect(region.locator('script')).toHaveCount(0)
    expect(await page.evaluate(() => 'reportExecuted' in window)).toBeFalsy()
    await region.locator('blockquote').scrollIntoViewIfNeeded()
    const details = page.getByRole('dialog')
    expect(await details.evaluate((dialog) => dialog.scrollWidth <= dialog.clientWidth)).toBeTruthy()
    await page.screenshot({ path: 'test-results/investigation-mobile.png' })
  })

  test('loads, retries, and hides reports when freshness cannot be verified', async ({ page }) => {
    let release!: () => void
    let state: 'failed' | 'ready' | 'stale' = 'failed'
    const gate = new Promise<void>((resolve) => { release = resolve })
    await page.route('**/api/runs/701/investigation', async (route) => {
      await gate
      await route.fulfill(state === 'failed' ? { status: 503, json: {} }
        : { json: state === 'ready' ? ready : { ...unavailable, status: 'stale' } })
    })
    await page.goto('/')
    await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
    const region = page.getByRole('region', { name: 'Investigation', exact: true })
    try {
      await expect(region.getByRole('status')).toHaveText('Loading investigation…')
    } finally {
      release()
    }
    await expect(region.getByRole('alert')).toContainText('We couldn’t load or verify the investigation.')
    state = 'ready'
    await region.getByRole('button', { name: 'Retry investigation' }).click()
    await expect(region.locator('blockquote')).toHaveCount(1)
    state = 'failed'
    await region.getByRole('button', { name: 'Refresh investigation' }).click()
    await expect(region.getByRole('alert')).toBeVisible()
    await expect(region.locator('blockquote')).toHaveCount(0)
    state = 'stale'
    await region.getByRole('button', { name: 'Retry investigation' }).click()
    await expect(region.getByRole('status')).toContainText('The stored report can no longer be verified.')
    await expect(region.locator('blockquote')).toHaveCount(0)
  })

  test('labels a published AI report and renders its evidence as plain text', async ({ page }) => {
    await page.route('**/api/runs/701/investigation', (route) => route.fulfill({
      json: { ...ready, generation_method: 'google-adk-v1' },
    }))
    await page.goto('/')
    await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
    const region = page.getByRole('region', { name: 'Investigation', exact: true })
    await expect(region).toContainText('AI-generated investigation · Approved draft')
    await expect(region).not.toContainText('No AI model was called.')
    await expect(region.locator('blockquote')).toHaveText(ready.report!.findings[0].citation.quote)
    await expect(region.locator('script')).toHaveCount(0)
    expect(await page.evaluate(() => 'reportExecuted' in window)).toBeFalsy()
    await expect(region.getByRole('button', { name: /approve|publish|generate/i })).toHaveCount(0)
  })

  test('distinguishes absent reports, insufficient evidence, and separate attempts', async ({ page }) => {
    let generated = false
    await page.route('**/api/runs/701/investigation', (route) => route.fulfill({ json: generated ? {
      ...ready, report: { ...ready.report, summary: 'Insufficient curated evidence to identify a failure diagnostic.', findings: [] },
    } : unavailable }))
    await page.goto('/')
    const rows = page.getByRole('table').locator('tbody tr')
    await rows.first().getByRole('button').click()
    const region = page.getByRole('region', { name: 'Investigation', exact: true })
    await expect(region).toContainText('No investigation has been generated')
    generated = true
    await region.getByRole('button', { name: 'Refresh investigation' }).click()
    await expect(region).toContainText('Insufficient curated evidence')
    await expect(region.locator('blockquote')).toHaveCount(0)
    await page.getByRole('button', { name: 'Close details' }).click()
    await rows.nth(1).getByRole('button').click()
    await expect(region).toContainText('No investigation has been generated')
    await expect(region).not.toContainText('Insufficient curated evidence')
  })
})

test('loads the seeded investigation through the real API', async ({ page }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: 'Repository', exact: true }).fill('demo/pipeline-assistant')
  await page.getByRole('button', { name: 'Apply filter' }).click()
  await page.getByRole('table').locator('tbody tr').filter({ hasText: '#1002' }).getByRole('button').click()
  const region = page.getByRole('region', { name: 'Investigation', exact: true })
  await expect(region).toContainText('HTTP status assertion failed')
  await expect(region).toContainText('Source line 6')
  await expect(region.locator('blockquote')).toHaveText('AssertionError: expected HTTP 200, received HTTP 503')
  await expect(region).toContainText('No AI model was called.')
})
