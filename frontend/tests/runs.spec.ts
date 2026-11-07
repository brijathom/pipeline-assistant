import { expect, test } from '@playwright/test'

import type { PipelineRun } from '../src/api/runs'

test('displays real API runs on desktop and mobile', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (error) => errors.push(error.message))

  const response = await page.request.get('/api/runs?limit=20')
  expect(response.ok()).toBeTruthy()
  const runs = await response.json() as PipelineRun[]
  expect(runs.length).toBeGreaterThan(0)

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
  await page.screenshot({ path: 'test-results/pipeline-runs-desktop.png', fullPage: true })

  await page.setViewportSize({ width: 390, height: 844 })
  await expect(page.getByRole('heading', { name: 'Pipeline runs', exact: true })).toBeVisible()
  await expect(page.getByRole('columnheader', { name: 'Conclusion', exact: true })).toBeVisible()
  await expect(rows.first().getByRole('cell').last()).toBeInViewport()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.screenshot({ path: 'test-results/pipeline-runs-mobile.png', fullPage: true })
  expect(errors).toEqual([])
})

test('shows an empty state when no runs exist', async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.goto('/')
  await expect(page.getByRole('heading', { name: 'No pipeline runs yet' })).toBeVisible()
  await expect(page.getByRole('table')).toHaveCount(0)
})

test('shows a loading state while waiting for the API', async ({ page }) => {
  let release!: () => void
  const responseGate = new Promise<void>((resolve) => { release = resolve })
  await page.route('**/api/runs?*', async (route) => {
    await responseGate
    await route.fulfill({ json: [] })
  })

  await page.goto('/')
  await expect(page.getByRole('status')).toContainText('Loading pipeline runs')
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
  await page.goto('/')
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(runs.length)
  await expect(page.getByRole('button', { name: 'Refresh', exact: true })).toBeEnabled()

  fail = true
  await page.getByRole('button', { name: 'Refresh', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Showing previously loaded runs')
  await expect(page.getByRole('table').locator('tbody tr')).toHaveCount(runs.length)
})
