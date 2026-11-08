import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { PrivateDraft, PrivateDraftDetails } from '../src/api/reviewer'

const token = 'fake-reviewer-token-' + 'x'.repeat(43)
const summary: PrivateDraft = {
  id: '11111111-1111-4111-8111-111111111111', pipeline_run_id: 4, repository: 'demo/pipeline-assistant',
  github_run_id: 1002, run_attempt: 1, status: 'pending_review', created_at: '2026-11-08T10:00:00Z',
}
const detail: PrivateDraftDetails = {
  ...summary, freshness: 'current', model: 'gemini-3.5-flash-lite', generated_at: '2026-11-08T09:59:00Z',
  report: {
    schema_version: 1, summary: 'Private draft marker: the health test received HTTP 503.',
    findings: [{ title: 'HTTP assertion', observation: 'Expected HTTP 200 but got HTTP 503.',
      recommendation: 'Read the test and service logs before changing configuration.',
      citation: { job_id: 3, job_name: 'Backend tests', source_line: 6,
        quote: 'AssertionError: expected HTTP 200, received HTTP 503 <script>window.privateExecuted=true</script>' } }],
    limitations: ['Only a truncated excerpt was available.'],
  },
}

async function openInbox(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Reviewer inbox', exact: true }).click()
  return page.getByRole('dialog', { name: 'Private draft inbox' })
}

async function unlock(page: Page, value = token) {
  await page.getByLabel('Reviewer access token', { exact: true }).fill(value)
  await page.getByRole('button', { name: 'Unlock inbox', exact: true }).click()
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 0, successful_runs: 0, failed_runs: 0, in_progress_runs: 0,
  } }))
  await page.route(/\/api\/reviewer\/drafts(?:\?|$)/, (route) => route.fulfill(
    route.request().headers()['authorization'] === `Bearer ${token}` ? { json: [summary] } : { status: 401, json: {} },
  ))
  await page.route(`**/api/reviewer/drafts/${summary.id}`, (route) => route.fulfill({ json: detail }))
})

test('loads private evidence with header authentication and no credential persistence', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const dialog = await openInbox(page)
  await unlock(page)
  await dialog.getByRole('button', { name: /demo\/pipeline-assistant/ }).click()
  await expect(dialog).toContainText('Private draft marker')
  await expect(dialog).toContainText('Source line 6')
  await expect(dialog.locator('blockquote')).toHaveText(detail.report!.findings[0].citation.quote)
  await expect(dialog.locator('script')).toHaveCount(0)
  expect(await page.evaluate(() => 'privateExecuted' in window)).toBeFalsy()
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy()
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain(token)
  expect(page.url()).not.toContain(token)
  await expect(dialog.getByRole('button', { name: /approve|reject|publish|generate/i })).toHaveCount(0)
})

test('rejects invalid credentials and clears the password field', async ({ page }) => {
  const dialog = await openInbox(page)
  await unlock(page, 'wrong-reviewer-' + 'y'.repeat(43))
  await expect(dialog.getByRole('alert')).toContainText('Access token was not accepted')
  await expect(dialog.getByLabel('Reviewer access token')).toHaveValue('')
  await expect(dialog).not.toContainText(summary.repository)
  await expect(dialog).not.toContainText('Private draft marker')
})

test('locking and closing remove private content and prevent cache reuse', async ({ page }) => {
  const dialog = await openInbox(page)
  await unlock(page)
  await dialog.getByRole('button', { name: /demo\/pipeline-assistant/ }).click()
  await expect(dialog).toContainText('Private draft marker')
  await dialog.getByRole('button', { name: 'Lock inbox', exact: true }).click()
  await expect(dialog).not.toContainText('Private draft marker')
  await unlock(page, 'wrong-reviewer-' + 'y'.repeat(43))
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog.locator('blockquote')).toHaveCount(0)
  await dialog.getByRole('button', { name: 'Close inbox' }).click()
  await page.getByRole('button', { name: 'Reviewer inbox', exact: true }).click()
  await expect(dialog.getByLabel('Reviewer access token')).toHaveValue('')
  await expect(dialog).not.toContainText('Private draft marker')
})

test('refresh failure and stale evidence hide previously loaded private reports', async ({ page }) => {
  let state: 'ready' | 'stale' | 'error' = 'ready'
  await page.route(`**/api/reviewer/drafts/${summary.id}`, (route) => route.fulfill(state === 'error'
    ? { status: 500, json: {} } : { json: { ...detail, freshness: state === 'stale' ? 'stale' : 'current' } }))
  const dialog = await openInbox(page)
  await unlock(page)
  await dialog.getByRole('button', { name: /demo\/pipeline-assistant/ }).click()
  await expect(dialog.locator('blockquote')).toHaveCount(1)
  state = 'error'
  await dialog.getByRole('button', { name: 'Refresh private report' }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog.locator('blockquote')).toHaveCount(0)
  state = 'stale'
  await dialog.getByRole('button', { name: 'Refresh private report' }).click()
  await expect(dialog).toContainText('This draft can no longer be verified')
  await expect(dialog.locator('blockquote')).toHaveCount(0)
})

test('authorization failure during a refresh locks the entire inbox', async ({ page }) => {
  let revoked = false
  await page.route(`**/api/reviewer/drafts/${summary.id}`, (route) => route.fulfill(revoked
    ? { status: 401, json: {} } : { json: detail }))
  const dialog = await openInbox(page)
  await unlock(page)
  await dialog.getByRole('button', { name: /demo\/pipeline-assistant/ }).click()
  await expect(dialog).toContainText('Private draft marker')
  revoked = true
  await dialog.getByRole('button', { name: 'Refresh private report' }).click()
  await expect(dialog.getByLabel('Reviewer access token')).toHaveValue('')
  await expect(dialog.locator('blockquote')).toHaveCount(0)
  await expect(dialog).not.toContainText(summary.repository)
})

test('empty and disabled inbox states have explicit messages', async ({ page }) => {
  let disabled = true
  await page.route(/\/api\/reviewer\/drafts(?:\?|$)/, (route) => route.fulfill(disabled
    ? { status: 503, json: {} } : { json: [] }))
  const dialog = await openInbox(page)
  await unlock(page)
  await expect(dialog.getByRole('alert')).toContainText('Reviewer access is unavailable')
  disabled = false
  await dialog.getByRole('button', { name: 'Retry inbox' }).click()
  await expect(dialog).toContainText('No drafts are awaiting review')
})
