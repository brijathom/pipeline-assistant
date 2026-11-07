import { expect, test } from '@playwright/test'

import type { PipelineJob } from '../src/api/jobs'
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

const storedJobs: PipelineJob[] = [
  {
    id: 801, github_job_id: 7001, name: 'Build (linux)', status: 'completed', conclusion: 'failure',
    started_at: firstRun.started_at, completed_at: firstRun.completed_at,
    steps: [
      { id: 901, step_number: 1, name: 'Set up job', status: 'completed', conclusion: 'success',
        started_at: firstRun.started_at, completed_at: '2026-11-07T12:00:20Z' },
      { id: 902, step_number: 7, name: 'Run tests', status: 'completed', conclusion: 'timed_out',
        started_at: '2026-11-07T12:00:20Z', completed_at: firstRun.completed_at },
    ],
  },
  { id: 802, github_job_id: 7002, name: 'Build (windows)', status: 'queued', conclusion: null,
    started_at: null, completed_at: null, steps: [] },
]

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [firstRun, secondRun] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 2, successful_runs: 0, failed_runs: 1, in_progress_runs: 1,
  } }))
  await page.route(/\/api\/runs\/70[12]\/jobs$/, (route) => route.fulfill({ json: [] }))
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
  await expect(details.getByRole('heading', { name: 'No stored jobs yet' })).toBeVisible()
  await expect(details.getByRole('button', { name: 'Close details' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(details).toHaveCount(0)
  await expect(trigger).toBeFocused()
})

test('displays ordered job evidence and automatically expands failed steps on mobile', async ({ page }) => {
  const successfulJob: PipelineJob = {
    ...storedJobs[0], id: 803, github_job_id: 7003, name: 'Build (macOS)', conclusion: 'success',
    steps: storedJobs[0].steps.map((step) => ({ ...step, id: step.id + 1000, conclusion: 'success' })),
  }
  await page.route('**/api/runs/701', (route) => route.fulfill({ json: firstRun }))
  await page.route('**/api/runs/701/jobs', (route) => route.fulfill({ json: [...storedJobs, successfulJob] }))
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  const evidence = details.getByRole('region', { name: 'Jobs and steps' })
  const linux = evidence.getByRole('article', { name: 'Build (linux)' })
  await expect(linux.getByRole('heading', { name: 'Step 7: Run tests', exact: true })).toBeVisible()
  await expect(linux.locator('details')).toHaveAttribute('open', '')
  await expect(linux.locator('ol h5')).toHaveText(['Step 1: Set up job', 'Step 7: Run tests'])
  const failedStep = linux.getByRole('listitem').filter({ has: page.getByRole('heading', { name: 'Step 7: Run tests', exact: true }) })
  await expect(failedStep).toContainText('Timed out')
  await expect(failedStep.locator('time').last()).toHaveAttribute('datetime', firstRun.completed_at!)
  const waiting = evidence.getByRole('article', { name: 'Build (windows)' })
  await expect(waiting.getByText('No stored steps for this job.')).toBeVisible()
  await expect(waiting.locator('dd')).toHaveText(['Not available', 'Not available'])
  const successful = evidence.getByRole('article', { name: 'Build (macOS)' })
  await expect(successful.locator('details')).not.toHaveAttribute('open', '')
  await successful.getByText('2 stored steps', { exact: true }).click()
  await expect(successful.getByRole('heading', { name: 'Step 7: Run tests', exact: true })).toBeVisible()
  await linux.getByRole('heading', { name: 'Build (linux)', exact: true }).scrollIntoViewIfNeeded()
  expect(await details.evaluate((dialog) => dialog.scrollWidth <= dialog.clientWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/job-evidence-mobile.png' })
})

test('job loading and retry do not hide run metadata', async ({ page }) => {
  let release!: () => void
  let fail = true
  const gate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/runs/701', (route) => route.fulfill({ json: firstRun }))
  await page.route('**/api/runs/701/jobs', async (route) => {
    await gate
    await route.fulfill(fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: storedJobs })
  })
  await page.goto('/')
  await page.getByRole('table').locator('tbody tr').first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  const evidence = details.getByRole('region', { name: 'Jobs and steps' })
  try {
    await expect(evidence.getByRole('status')).toHaveText('Loading job evidence…')
    await expect(details.getByRole('heading', { name: 'Build and test', exact: true })).toBeVisible()
  } finally {
    release()
  }
  await expect(evidence.getByRole('alert')).toContainText('We couldn’t load job evidence')
  await expect(details.getByRole('heading', { name: 'Build and test', exact: true })).toBeVisible()
  fail = false
  await evidence.getByRole('button', { name: 'Retry jobs' }).click()
  await expect(evidence.getByRole('alert')).toHaveCount(0)
  await expect(evidence.getByRole('article')).toHaveCount(2)
})

test('failed job refresh preserves evidence and switching attempts clears it', async ({ page }) => {
  let fail = false
  await page.route(/\/api\/runs\/70[12]$/, (route) => route.fulfill({
    json: route.request().url().endsWith('/701') ? firstRun : secondRun,
  }))
  await page.route('**/api/runs/701/jobs', (route) => route.fulfill(
    fail ? { status: 503, json: { detail: 'Unavailable' } } : { json: storedJobs },
  ))
  await page.goto('/')
  const rows = page.getByRole('table').locator('tbody tr')
  await rows.first().getByRole('button').click()
  const details = page.getByRole('dialog', { name: 'Run details' })
  const evidence = details.getByRole('region', { name: 'Jobs and steps' })
  await expect(evidence.getByRole('article')).toHaveCount(2)
  fail = true
  await details.getByRole('button', { name: 'Refresh details', exact: true }).click()
  await expect(evidence.getByRole('alert')).toContainText('Showing previously loaded evidence')
  await expect(evidence.getByRole('article')).toHaveCount(2)
  await details.getByRole('button', { name: 'Close details' }).click()
  await expect(details).toHaveCount(0)
  await rows.nth(1).getByRole('button').click()
  await expect(evidence.getByRole('heading', { name: 'No stored jobs yet' })).toBeVisible()
  await expect(evidence.getByRole('article')).toHaveCount(0)
  await expect(evidence.getByRole('alert')).toHaveCount(0)
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
