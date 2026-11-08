import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { InvestigationTask } from '../src/api/reviewer'

const token = 'fake-task-reviewer-' + 'x'.repeat(43)
const id = '11111111-1111-4111-8111-111111111111'
const task: InvestigationTask = {
  id, pipeline_run_id: 4, repository: 'demo/pipeline-assistant', github_run_id: 1002, run_attempt: 1,
  status: 'queued', model: 'gemini-3.5-flash-lite', prompt_version: 'investigation-v1',
  created_at: '2026-11-08T10:00:00Z', started_at: null, completed_at: null, draft_id: null, error_code: null,
  dispatch_exhausted: false,
}

test.beforeEach(async ({ page }) => {
  await page.route('**/api/runs?*', (route) => route.fulfill({ json: [{
    id: 4, repository: 'demo/pipeline-assistant', github_run_id: 1002, run_attempt: 1, workflow_name: 'CI',
    status: 'completed', conclusion: 'failure', created_at: task.created_at, started_at: null, completed_at: null,
  }] }))
  await page.route(/\/api\/metrics(?:\?|$)/, (route) => route.fulfill({ json: {
    repository: null, total_runs: 1, successful_runs: 0, failed_runs: 1, in_progress_runs: 0,
  } }))
  await page.route(/\/api\/reviewer\/drafts(?:\?|$)/, (route) => route.fulfill({ json: [] }))
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill({ json: [] }))
})

async function openTasks(page: Page) {
  await page.goto('/')
  await page.getByRole('button', { name: 'Reviewer inbox', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Private draft inbox' })
  await dialog.getByLabel('Reviewer access token', { exact: true }).fill(token)
  await dialog.getByRole('button', { name: 'Unlock inbox', exact: true }).click()
  await dialog.getByRole('button', { name: 'Investigation tasks', exact: true }).click()
  return dialog
}

test('stopped automatic delivery shows operator recovery without resubmitting', async ({ page }) => {
  let posts = 0
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill({ json: [{ ...task, dispatch_exhausted: true }] }))
  await page.route('**/api/reviewer/tasks', (route) => { posts += 1; return route.fulfill({ status: 500 }) })
  const dialog = await openTasks(page)
  await expect(dialog.getByRole('alert')).toContainText('Automatic delivery stopped after repeated failures')
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await expect(dialog.getByRole('button', { name: 'Queue investigation', exact: true })).toBeDisabled()
  expect(posts).toBe(0)
})

test('queues once, polls lifecycle status and leads to private draft review', async ({ page }) => {
  let current: InvestigationTask | null = null
  let posts = 0
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill({ json: current ? [current] : [] }))
  await page.route('**/api/reviewer/tasks', (route) => {
    posts += 1
    expect(route.request().headers()['authorization']).toBe(`Bearer ${token}`)
    const body = route.request().postDataJSON()
    expect(body.run_id).toBe(4)
    expect(body.request_id).toMatch(/^[0-9a-f-]{36}$/)
    expect(Object.keys(body).sort()).toEqual(['request_id', 'run_id'])
    current = { ...task, id: body.request_id }
    return route.fulfill({ status: 202, json: current })
  })
  const dialog = await openTasks(page)
  await expect(dialog).toContainText('No investigation tasks yet')
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await dialog.getByRole('button', { name: 'Queue investigation', exact: true }).click()
  await expect(dialog).toContainText('Status: queued')
  current = { ...current!, status: 'running', started_at: task.created_at }
  await expect(dialog).toContainText('Status: running')
  current = { ...current, status: 'succeeded', completed_at: task.created_at, draft_id: id }
  await expect(dialog).toContainText('Status: succeeded')
  expect(posts).toBe(1)
  await dialog.getByRole('button', { name: 'Review drafts', exact: true }).click()
  await expect(dialog.getByRole('heading', { name: 'Pending drafts', exact: true })).toBeVisible()
})

test('an unconfirmed submission is not automatically retried and explicit retry keeps its ID', async ({ page }) => {
  const ids: string[] = []
  let accepted: InvestigationTask | null = null
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill({ json: accepted ? [accepted] : [] }))
  await page.route('**/api/reviewer/tasks', (route) => {
    const body = route.request().postDataJSON()
    ids.push(body.request_id)
    if (ids.length === 1) return route.fulfill({ status: 503, json: {} })
    accepted = { ...task, id: body.request_id }
    return route.fulfill({ status: 202, json: accepted })
  })
  const dialog = await openTasks(page)
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await dialog.getByRole('button', { name: 'Queue investigation', exact: true }).click()
  await expect(dialog.getByRole('alert')).toContainText('task submission could not be confirmed')
  await expect(dialog.getByLabel('Run to investigate', { exact: true })).toBeDisabled()
  expect(ids).toHaveLength(1)
  await dialog.getByRole('button', { name: 'Retry same request', exact: true }).click()
  await expect(dialog).toContainText('Status: queued')
  expect(ids).toHaveLength(2)
  expect(ids[0]).toBe(ids[1])
})

test('existing active tasks prevent another submission for the same run', async ({ page }) => {
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill({ json: [task] }))
  const dialog = await openTasks(page)
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await expect(dialog.getByRole('button', { name: 'Queue investigation', exact: true })).toBeDisabled()
  await expect(dialog).toContainText('This run already has an active task')
})

test('failed status refresh hides previously loaded task metadata', async ({ page }) => {
  let fail = false
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill(fail ? { status: 500, json: {} } : { json: [task] }))
  const dialog = await openTasks(page)
  await expect(dialog).toContainText(id)
  fail = true
  await dialog.getByRole('button', { name: 'Refresh tasks', exact: true }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog).not.toContainText(id)
})

test('authorization failure locks the task view and removes private metadata', async ({ page }) => {
  let denied = false
  await page.route('**/api/reviewer/tasks?*', (route) => route.fulfill(denied ? { status: 401, json: {} } : { json: [task] }))
  const dialog = await openTasks(page)
  await expect(dialog).toContainText(id)
  denied = true
  await dialog.getByRole('button', { name: 'Refresh tasks', exact: true }).click()
  await expect(dialog.getByLabel('Reviewer access token', { exact: true })).toHaveValue('')
  await expect(dialog).not.toContainText(id)
})

test('a late submission response cannot restore a locked task view', async ({ page }) => {
  let release!: () => void
  let received!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  const started = new Promise<void>((resolve) => { received = resolve })
  await page.route('**/api/reviewer/tasks', async (route) => {
    received()
    await gate
    await route.fulfill({ status: 202, json: task }).catch(() => {})
  })
  const dialog = await openTasks(page)
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  try {
    await dialog.getByRole('button', { name: 'Queue investigation', exact: true }).click()
    await started
    await dialog.getByRole('button', { name: 'Lock inbox', exact: true }).click()
  } finally { release() }
  await expect(dialog.getByLabel('Reviewer access token', { exact: true })).toHaveValue('')
  await expect(dialog).not.toContainText(id)
})
