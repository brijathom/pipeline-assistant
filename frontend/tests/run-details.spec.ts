import { expect, test } from '@playwright/test'

import type { PipelineRun } from '../src/api/runs'

const firstRun: PipelineRun = {
  id: 701, repository: 'example/project', github_run_id: 1234, run_attempt: 1,
  workflow_name: 'Build and test', status: 'completed', conclusion: 'failure',
  created_at: '2026-11-07T12:00:00Z', started_at: '2026-11-07T12:00:10Z',
  completed_at: '2026-11-07T12:03:00Z',
}
const secondRun: PipelineRun = {
  ...firstRun, id: 702, run_attempt: 2, status: 'in_progress', conclusion: null,
  started_at: null, completed_at: null,
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [firstRun, secondRun] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 2, successful_runs: 0, failed_runs: 1, in_progress_runs: 1,
  } }))
})

test('loads details and returns keyboard focus when closed', async ({ page }) => {
  let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/runs/701', async (route) => {
    await gate
    await route.fulfill({ json: firstRun })
  })
  await page.goto('/')
  const trigger = page.getByRole('table').locator('tbody tr').first().getByRole('button')
  await trigger.focus()
  await page.keyboard.press('Enter')
  const details = page.getByRole('dialog', { name: 'Run details' })
  try {
    await expect(details.getByRole('status')).toHaveText('Loading run details…')
  } finally {
    release()
  }
  await expect(details.getByRole('heading', { name: 'Build and test' })).toBeVisible()
  await expect(details.getByText('Repository', { exact: true }).locator('..').locator('dd')).toHaveText('example/project')
  await expect(details.getByText('Conclusion', { exact: true }).locator('..').locator('dd')).toHaveText('Failure')
  const completedField = details.locator('dt', { hasText: 'Completed' }).locator('..')
  await expect(completedField.locator('time')).toHaveAttribute('datetime', firstRun.completed_at!)
  await expect(completedField.locator('dd')).toContainText('12:03:00 UTC')
  await expect(details.getByRole('button', { name: 'Close details' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(details).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('shows a specific not-found state', async ({ page }) => {
  await page.route('**/api/runs/701', (route) => route.fulfill({ status: 404, json: { detail: 'Pipeline run not found' } }))
  await page.goto('/')
  await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  await expect(details.getByRole('alert')).toContainText('Run not found')
  await expect(details.locator('dl')).toHaveCount(0)
  await expect(details.getByRole('button', { name: 'Retry details' })).toHaveCount(0)
  await details.getByRole('button', { name: 'Close details' }).click()
  await expect(details).toHaveCount(0)
})

test('recovers from a temporary details error', async ({ page }) => {
  let fail = true
  await page.route('**/api/runs/701', (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: firstRun },
  ))
  await page.goto('/')
  await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  await expect(details.getByRole('alert')).toContainText('We couldn’t load run details')
  fail = false
  await details.getByRole('button', { name: 'Retry details' }).click()
  await expect(details.getByRole('alert')).toHaveCount(0)
  await expect(details.getByRole('heading', { name: 'Build and test' })).toBeVisible()
})

test('retains stale details on refresh failure and keeps attempts separate', async ({ page }) => {
  let fail = false
  await page.route(/\/api\/runs\/70[12]$/, (route) => {
    const run = route.request().url().endsWith('/701') ? firstRun : secondRun
    return route.fulfill(fail && run.id === 701
      ? { status: 503, json: { detail: 'Unavailable' } }
      : { json: run })
  })
  await page.goto('/')
  const rows = page.getByRole('table').locator('tbody tr')
  await rows.first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  await expect(details.getByText('Attempt', { exact: true }).locator('..').locator('dd')).toHaveText('1')
  fail = true
  await details.getByRole('button', { name: 'Refresh details', exact: true }).click()
  await expect(details.getByRole('alert')).toContainText('Showing previously loaded data')
  await expect(details.getByText('Conclusion', { exact: true }).locator('..').locator('dd')).toHaveText('Failure')
  await details.getByRole('button', { name: 'Close details' }).click()
  await expect(details).toHaveCount(0)
  await rows.nth(1).getByRole('button').click()
  await expect(details.getByText('Attempt', { exact: true }).locator('..').locator('dd')).toHaveText('2')
  await expect(details.getByRole('alert')).toHaveCount(0)
  await expect(details.getByText('Status', { exact: true }).locator('..').locator('dd')).toHaveText('In progress')
  for (const label of ['Started', 'Completed', 'Conclusion']) {
    await expect(details.getByText(label, { exact: true }).locator('..').locator('dd')).toHaveText('Not available')
  }
})
