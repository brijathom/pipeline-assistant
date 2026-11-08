import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { PrivateLogDetails } from '../src/api/reviewer'

const token = 'fake-reviewer-token-' + 'x'.repeat(43)
const log: PrivateLogDetails = {
  job_id: 7, pipeline_run_id: 4, repository: 'example/project', github_run_id: 1002, run_attempt: 2,
  job_name: 'Backend tests', is_curated: false, updated_at: '2026-11-08T10:00:00Z', revision: 'a'.repeat(64),
  excerpt: { lines: [{ source_line: 6, text: 'FAILED <script>window.logExecuted=true</script>' }],
    total_source_lines: 120, truncated: true, redactions_applied: true, source_kind: 'github', updated_at: '2026-11-08T10:00:00Z' },
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 0, successful_runs: 0, failed_runs: 0, in_progress_runs: 0,
  } }))
  await page.route(/\/api\/reviewer\/drafts(?:\?|$)/, (route) => route.fulfill({ json: [] }))
  await page.route(/\/api\/reviewer\/logs(?:\?|$)/, (route) => route.fulfill({ json: [log] }))
  await page.route('**/api/reviewer/logs/7', (route) => route.fulfill({ json: log }))
})

async function openLogs(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Reviewer inbox', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Private draft inbox' })
  await dialog.getByLabel('Reviewer access token', { exact: true }).fill(token)
  await dialog.getByRole('button', { name: 'Unlock inbox', exact: true }).click()
  await dialog.getByRole('button', { name: 'Imported logs', exact: true }).click()
  return dialog
}

test('reviews numbered plain text, then curates and withdraws explicitly', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  let current = log
  let requests = 0
  await page.route('**/api/reviewer/logs/7', (route) => route.fulfill({ json: current }))
  await page.route('**/api/reviewer/logs/7/curation', (route) => {
    requests += 1
    expect(route.request().headers()['authorization']).toBe(`Bearer ${token}`)
    expect(route.request().postDataJSON()).toEqual({ expected_revision: current.revision, is_curated: !current.is_curated })
    current = { ...current, is_curated: !current.is_curated, revision: (requests === 1 ? 'b' : 'c').repeat(64) }
    return route.fulfill({ json: current })
  })
  const dialog = await openLogs(page)
  await dialog.getByRole('button', { name: /example\/project/ }).click()
  await expect(dialog.getByLabel('Private log source lines')).toContainText(log.excerpt.lines[0].text)
  await expect(dialog.getByLabel('Source line 6', { exact: true })).toBeVisible()
  expect(await page.evaluate(() => 'logExecuted' in window)).toBeFalsy()
  expect(await dialog.evaluate((element) => element.scrollWidth <= element.clientWidth)).toBeTruthy()
  const curate = dialog.getByRole('button', { name: 'Curate excerpt', exact: true })
  await expect(curate).toBeDisabled()
  expect(requests).toBe(0)
  await dialog.getByRole('checkbox').check()
  await curate.click()
  await expect(dialog).toContainText('Curated: available publicly')
  await dialog.getByRole('button', { name: 'Withdraw curation', exact: true }).click()
  await expect(dialog).toContainText('Uncurated: hidden publicly')
  await expect(dialog.getByRole('checkbox')).not.toBeChecked()
  await expect(curate).toBeDisabled()
  expect(requests).toBe(2)
  expect(await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage))).not.toContain(token)
  await dialog.getByRole('button', { name: 'Lock inbox', exact: true }).click()
  await expect(dialog.getByLabel('Private log source lines')).toHaveCount(0)
})

test('changed content requires a new review after a conflict and never retries the write', async ({ page }) => {
  let changed = false
  let requests = 0
  await page.route('**/api/reviewer/logs/7', (route) => route.fulfill({ json: changed
    ? { ...log, revision: 'b'.repeat(64), excerpt: { ...log.excerpt, lines: [{ source_line: 6, text: 'Changed excerpt' }] } } : log }))
  await page.route('**/api/reviewer/logs/7/curation', (route) => {
    requests += 1
    changed = true
    return route.fulfill({ status: 409, json: {} })
  })
  const dialog = await openLogs(page)
  await dialog.getByRole('button', { name: /example\/project/ }).click()
  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('button', { name: 'Curate excerpt', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('excerpt changed')
  await expect(dialog.getByLabel('Private log source lines')).toContainText('Changed excerpt')
  await expect(dialog.getByRole('checkbox')).not.toBeChecked()
  await expect(dialog.getByRole('button', { name: 'Curate excerpt', exact: true })).toBeDisabled()
  expect(requests).toBe(1)
})

test('uncertain curation reloads committed status without retrying', async ({ page }) => {
  let committed = false
  let requests = 0
  await page.route('**/api/reviewer/logs/7', (route) => route.fulfill({ json: { ...log, is_curated: committed, revision: (committed ? 'b' : 'a').repeat(64) } }))
  await page.route('**/api/reviewer/logs/7/curation', (route) => {
    requests += 1
    committed = true
    return route.fulfill({ status: 500, json: {} })
  })
  const dialog = await openLogs(page)
  await dialog.getByRole('button', { name: /example\/project/ }).click()
  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('button', { name: 'Curate excerpt', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('curation could not be confirmed')
  await expect(dialog).toContainText('Curated: available publicly')
  expect(requests).toBe(1)
})

test('failed private refresh hides previously loaded content', async ({ page }) => {
  let failed = false
  await page.route('**/api/reviewer/logs/7', (route) => route.fulfill(failed ? { status: 500, json: {} } : { json: log }))
  const dialog = await openLogs(page)
  await dialog.getByRole('button', { name: /example\/project/ }).click()
  await expect(dialog.getByLabel('Private log source lines')).toBeVisible()
  failed = true
  await dialog.getByRole('button', { name: 'Refresh private excerpt' }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog.getByLabel('Private log source lines')).toHaveCount(0)
})

test('authorization failure during curation locks the inbox', async ({ page }) => {
  await page.route('**/api/reviewer/logs/7/curation', (route) => route.fulfill({ status: 401, json: {} }))
  const dialog = await openLogs(page)
  await dialog.getByRole('button', { name: /example\/project/ }).click()
  await dialog.getByRole('checkbox').check()
  await dialog.getByRole('button', { name: 'Curate excerpt', exact: true }).click()
  await expect(dialog.getByLabel('Reviewer access token', { exact: true })).toHaveValue('')
  await expect(dialog.getByLabel('Private log source lines')).toHaveCount(0)
})

test('empty log queue is explicit', async ({ page }) => {
  await page.route(/\/api\/reviewer\/logs(?:\?|$)/, (route) => route.fulfill({ json: [] }))
  const dialog = await openLogs(page)
  await expect(dialog).toContainText('No imported excerpts with this status')
})
