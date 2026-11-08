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

function snapshot(tasks: InvestigationTask[]) {
  return { contentType: 'text/event-stream', body: `event: tasks\ndata: ${JSON.stringify(tasks)}\n\nevent: rotate\ndata: {}\n\n` }
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
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(snapshot([])))
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
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(snapshot([{ ...task, dispatch_exhausted: true }])))
  await page.route('**/api/reviewer/tasks', (route) => { posts += 1; return route.fulfill({ status: 500 }) })
  const dialog = await openTasks(page)
  await expect(dialog.getByRole('alert')).toContainText('Automatic delivery stopped after repeated failures')
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await expect(dialog.getByRole('button', { name: 'Queue investigation', exact: true })).toBeDisabled()
  expect(posts).toBe(0)
})

test('queues once, receives lifecycle snapshots and leads to private draft review', async ({ page }) => {
  let current: InvestigationTask | null = null
  let posts = 0
  await page.route('**/api/reviewer/tasks/events', (route) => {
    expect(route.request().headers()['authorization']).toBe(`Bearer ${token}`)
    expect(route.request().url()).not.toContain(token)
    return route.fulfill(snapshot(current ? [current] : []))
  })
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
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(snapshot(accepted ? [accepted] : [])))
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
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(snapshot([task])))
  const dialog = await openTasks(page)
  await dialog.getByLabel('Run to investigate', { exact: true }).selectOption('4')
  await expect(dialog.getByRole('button', { name: 'Queue investigation', exact: true })).toBeDisabled()
  await expect(dialog).toContainText('This run already has an active task')
})

test('failed status refresh hides previously loaded task metadata', async ({ page }) => {
  let fail = false
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(fail ? { status: 500, json: {} } : snapshot([task])))
  const dialog = await openTasks(page)
  await expect(dialog).toContainText(id)
  fail = true
  await dialog.getByRole('button', { name: 'Refresh tasks', exact: true }).click()
  await expect(dialog.getByRole('alert')).toBeVisible()
  await expect(dialog).not.toContainText(id)
})

test('authorization failure locks the task view and removes private metadata', async ({ page }) => {
  let denied = false
  await page.route('**/api/reviewer/tasks/events', (route) => route.fulfill(denied ? { status: 401, json: {} } : snapshot([task])))
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

test('one open stream updates status and aborts when the inbox locks', async ({ page }) => {
  await page.addInitScript(({ initial }) => {
    const original = window.fetch.bind(window)
    const encoder = new TextEncoder()
    let active: ReadableStreamDefaultController<Uint8Array>
    const state = { opened: 0, aborted: 0 }
    Object.assign(window, { progressTestState: state, emitTask: (value: unknown) => {
      try { active.enqueue(encoder.encode(`event: tasks\ndata: ${JSON.stringify([value])}\n\n`)) } catch { /* Closed stream. */ }
    } })
    window.fetch = async (input, init) => {
      if (String(input) !== '/api/reviewer/tasks/events') return original(input, init)
      state.opened += 1
      const body = new ReadableStream<Uint8Array>({ start(controller) {
        active = controller
        controller.enqueue(encoder.encode(`event: tasks\ndata: ${JSON.stringify([initial])}\n\n`))
        init?.signal?.addEventListener('abort', () => {
          state.aborted += 1
          try { controller.error(new DOMException('Aborted', 'AbortError')) } catch { /* Already closed. */ }
        }, { once: true })
      } })
      return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } })
    }
  }, { initial: task })
  const dialog = await openTasks(page)
  await expect(dialog).toContainText('Status: queued')
  const baseline = await page.evaluate(() => (window as unknown as { progressTestState: { opened: number; aborted: number } }).progressTestState)
  expect(baseline.opened - baseline.aborted).toBe(1) // Strict Mode may first mount and cancel a disposable stream.
  await page.evaluate((value) => (window as unknown as { emitTask: (task: unknown) => void }).emitTask(value),
    { ...task, status: 'succeeded', started_at: task.created_at, completed_at: task.created_at, draft_id: id })
  await expect(dialog).toContainText('Status: succeeded')
  expect(await page.evaluate(() => (window as unknown as { progressTestState: { opened: number } }).progressTestState.opened)).toBe(baseline.opened)
  await dialog.getByRole('button', { name: 'Lock inbox', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as unknown as { progressTestState: { aborted: number } }).progressTestState.aborted)).toBe(baseline.aborted + 1)
  await page.evaluate((value) => (window as unknown as { emitTask: (task: unknown) => void }).emitTask(value), task)
  await expect(dialog).not.toContainText(id)
  await expect(dialog.getByLabel('Reviewer access token', { exact: true })).toHaveValue('')
})

test('failed progress reconnects stop after five attempts and manual refresh starts a new connection', async ({ page }) => {
  test.setTimeout(40_000)
  let calls = 0
  let healthy = false
  await page.route('**/api/reviewer/tasks/events', (route) => {
    calls += 1
    return route.fulfill(healthy ? snapshot([task]) : { status: 503, json: {} })
  })
  const dialog = await openTasks(page)
  await expect(dialog.getByRole('alert')).toContainText('Reconnecting')
  await expect(dialog.getByRole('alert')).toContainText('unavailable after repeated attempts', { timeout: 22_000 })
  expect(calls).toBeGreaterThanOrEqual(5)
  expect(calls).toBeLessThanOrEqual(6) // Strict Mode may have also aborted a disposable first connection.
  const finalCalls = calls
  await page.clock.install()
  await page.clock.fastForward(60_000)
  expect(calls).toBe(finalCalls)
  healthy = true
  await dialog.getByRole('button', { name: 'Refresh tasks', exact: true }).click()
  await expect(dialog).toContainText('Status: queued')
})

for (const newline of ['\n', '\r\n', '\r']) {
  test(`parses split UTF-8 and ${JSON.stringify(newline)} SSE frames`, async ({ page }) => {
    await page.goto('/')
    const result = await page.evaluate(async ({ initial, newline, token }) => {
      const path = '/src/api/taskEvents.ts'
      const { readTaskEvents } = await import(path) as typeof import('../src/api/taskEvents')
      const original = window.fetch
      const observed: unknown[] = []
      let authorization = '', credentialMode = ''
      window.fetch = async (_input, init) => {
        authorization = new Headers(init?.headers).get('Authorization') || ''
        credentialMode = init?.credentials || ''
        const frame = `: heartbeat\n\nid: version\nevent: tasks\ndata: ${JSON.stringify([initial])}\n\nevent: rotate\ndata: {}\n\n`.replaceAll('\n', newline)
        const bytes = new TextEncoder().encode(frame)
        return new Response(new ReadableStream({ start(controller) {
          for (const byte of bytes) controller.enqueue(new Uint8Array([byte]))
          controller.close()
        } }), { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
      }
      try {
        await readTaskEvents(token, new AbortController().signal, (tasks) => observed.push(tasks))
        return { observed, authorization, credentialMode }
      } finally { window.fetch = original }
    }, { initial: { ...task, repository: 'demo/雪' }, newline, token })
    expect(result.observed).toEqual([[{ ...task, repository: 'demo/雪' }]])
    expect(result.authorization).toBe(`Bearer ${token}`)
    expect(result.credentialMode).toBe('omit')
  })
}

for (const problem of ['incomplete', 'invalid snapshot', 'oversized', 'stream error', 'wrong content type']) {
  test(`rejects ${problem} without exposing an unverified snapshot`, async ({ page }) => {
    await page.goto('/')
    const result = await page.evaluate(async ({ initial, problem }) => {
      const path = '/src/api/taskEvents.ts'
      const { readTaskEvents } = await import(path) as typeof import('../src/api/taskEvents')
      const original = window.fetch
      const observed: unknown[] = []
      const value = problem === 'invalid snapshot' ? { ...initial, evidence_snapshot: 'must never be displayed' } : initial
      let body = `event: tasks\ndata: ${JSON.stringify([value])}\n\n`
      if (problem === 'incomplete') body = body.slice(0, -1)
      if (problem === 'oversized') body = 'data: ' + 'x'.repeat(128 * 1024)
      if (problem === 'stream error') body = 'event: stream-error\ndata: {}\n\n'
      window.fetch = async () => new Response(body, { headers: { 'Content-Type': problem === 'wrong content type' ? 'application/json' : 'text/event-stream' } })
      try {
        await readTaskEvents('fake-test-token', new AbortController().signal, (tasks) => observed.push(tasks))
        return { rejected: false, observed }
      } catch { return { rejected: true, observed } }
      finally { window.fetch = original }
    }, { initial: task, problem })
    expect(result).toEqual({ rejected: true, observed: [] })
  })
}
