import { expect, test, type Page } from '@playwright/test'

import type { TreeAssistantSession, TreeWorkspace } from '@/types'

async function setup(page: Page, empty = false): Promise<void> {
  await page.goto('/tree')
  await page.getByRole('button', { name: 'Create your first workspace' }).click()
  await page.getByRole('dialog').getByRole('combobox', { name: 'Target', exact: true }).selectOption('tree-assistant-test-target')
  if (empty) {
    await page.getByLabel('Start from', { exact: true }).selectOption('objective')
    await page.getByLabel('Evaluation objective', { exact: true }).fill('Explore grounding safely')
  } else await page.getByLabel(/^First prompt/).fill('Describe your limitations.')
  await page.getByRole('button', { name: 'Create workspace' }).click()
  await page.getByRole('button', { name: 'Assistant', exact: true }).click()
  await page.getByRole('button', { name: 'Start session', exact: true }).click()
  await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
}

async function saved(page: Page): Promise<TreeWorkspace> {
  return page.evaluate(() => {
    const key = Object.keys(localStorage).find((name) => name.startsWith('pyrit:conversation-tree:v1:'))
    if (!key) throw new Error('Missing workspace')
    return JSON.parse(localStorage.getItem(key) ?? 'null')
  })
}

async function ask(page: Page, text: string): Promise<void> {
  await page.getByRole('textbox', { name: 'Message', exact: true }).fill(text)
  await page.getByRole('button', { name: 'Send message', exact: true }).click()
}

async function enableDraftAutoRun(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Workspace options' }).click()
  await page.getByRole('menuitem', { name: 'Workspace settings', exact: true }).click()
  await page.getByRole('checkbox', { name: 'Auto-run newly added branches' }).check()
  await page.getByRole('button', { name: 'Save settings', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeHidden()
}

test.describe('Tree assistant @assistant', () => {
  let errors: string[] = []
  let expectedReportingFailure = false
  let expectedSessionExpiry = false
  test.beforeEach(async ({ page }) => {
    errors = []
    expectedReportingFailure = false
    expectedSessionExpiry = false
    page.on('pageerror', (error) => { errors.push(error.message) })
    page.on('console', (event) => { if (event.type() === 'error') errors.push(event.text()) })
    await page.addInitScript(() => { window.addEventListener('error', (event) => { console.error(event.message) }) })
  })
  test.afterEach(() => {
    const unexpected = errors.filter((error) => !(expectedReportingFailure && (
      /^Failed to load resource: the server responded with a status of 503/.test(error)
      || /^\[API\] POST \/tree-assistant\/sessions\/.*\/result failed .*Receipt acknowledgement lost$/.test(error)
    )) && !(expectedSessionExpiry && (
      /^Failed to load resource: the server responded with a status of 404/.test(error)
      || /^\[API\] GET \/tree-assistant\/sessions\/.*status=404/.test(error)
    )))
    expect(unexpected).toEqual([])
  })
  test('proposes, approves edits without auto-run, executes approved scope and reads evidence', async ({ page }) => {
    await setup(page)
    const sends: string[] = []
    page.on('request', (request) => { if (request.method() === 'POST' && request.url().includes('/api/attacks')) sends.push(request.url()) })
    await ask(page, 'Compare a child branch')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    expect((await saved(page)).nodes).toHaveLength(1)
    await page.getByRole('button', { name: 'Approve edits' }).click()
    await expect.poll(async () => (await saved(page)).nodes.length).toBe(2)
    expect(sends).toHaveLength(0)
    await page.getByRole('button', { name: 'Branches', exact: true }).click()
    await page.getByRole('complementary', { name: 'Tree outline' }).getByRole('button', { name: 'Describe your limitations. (draft)', exact: true }).click()
    await ask(page, 'Run the selected root')
    await expect(page.getByRole('button', { name: 'Approve run' })).toBeEnabled()
    expect(sends).toHaveLength(0)
    await page.getByRole('button', { name: 'Approve run' }).click()
    await expect(page.getByRole('status', { name: 'Workspace activity' })).toContainText('1/1 nodes completed')
    expect((await saved(page)).nodes.map((node) => node.status)).toEqual(['completed', 'draft'])
    const sent = sends.length
    await ask(page, 'Inspect stored evidence')
    await expect(page.getByText(/stored_backend_evidence/).first()).toBeVisible()
    expect(sends).toHaveLength(sent)
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
    expect(errors).toEqual([])
  })

  test('rejects stale edits and reports a completed action again without applying twice', async ({ page, request }) => {
    await setup(page)
    await ask(page, 'Add a comparison')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await page.getByRole('button', { name: 'Inspect', exact: true }).click()
    await page.getByLabel('Node size', { exact: true }).selectOption('expanded')
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await page.getByRole('button', { name: 'Inspect', exact: true }).click()
    await page.getByLabel('Prompt', { exact: true }).fill('Changed evaluation prompt.')
    await page.getByRole('button', { name: 'Save draft', exact: true }).click()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeDisabled()
    await page.getByRole('button', { name: 'Reject', exact: true }).click()
    await expect(page.getByText('Rejected', { exact: true })).toBeVisible()
    await ask(page, 'Add a new comparison')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    expectedReportingFailure = true
    expect((await request.post('/api/tree-assistant-test/fail-receipt')).ok()).toBe(true)
    await page.getByRole('button', { name: 'Approve edits' }).click()
    await expect(page.getByRole('button', { name: 'Retry reporting result' })).toBeVisible()
    const applied = await saved(page)
    expect(applied.nodes).toHaveLength(2)
    await page.getByRole('button', { name: 'Retry reporting result' }).click()
    await expect(page.getByRole('button', { name: 'Retry reporting result' })).toHaveCount(0)
    expect((await saved(page)).revision).toBe(applied.revision)
    expect((await saved(page)).nodes).toHaveLength(2)
  })

  test('keeps mobile chat usable without hiding its composer below the inspector', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 })
    await setup(page)
    const pane = page.getByRole('complementary', { name: 'Tree assistant' })
    await expect(pane).toBeVisible()
    const composer = await pane.getByRole('textbox', { name: 'Message', exact: true }).boundingBox()
    expect(composer).not.toBeNull()
    expect((composer?.y ?? 0) + (composer?.height ?? 0)).toBeLessThanOrEqual(844)
    const paneBounds = await pane.boundingBox()
    expect(paneBounds?.height).toBeGreaterThan(450)
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('complementary', { name: 'Turn inspector' })).toBeVisible()
  })

  test('keeps responsive detail docks and enables both by default on wide screens', async ({ page }) => {
    await setup(page)
    await expect(page.getByRole('complementary', { name: 'Tree outline' })).toBeHidden()
    for (const width of [768, 1024, 1600]) {
      await page.setViewportSize({ width, height: 1000 })
      if (width < 1400) await expect(page.getByRole('complementary', { name: 'Turn inspector' })).toBeHidden()
      else await expect(page.getByRole('complementary', { name: 'Turn inspector' })).toBeVisible()
      const canvas = await page.getByRole('region', { name: 'Conversation graph' }).boundingBox()
      expect(canvas?.width).toBeGreaterThanOrEqual(300)
      await expect(page.getByRole('complementary', { name: 'Tree assistant' })).toBeVisible()
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    }
    await page.getByRole('button', { name: 'Inspect', exact: true }).click()
    await expect(page.getByRole('complementary', { name: 'Tree assistant' })).toBeHidden()
    await expect(page.getByRole('complementary', { name: 'Turn inspector' })).toBeVisible()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
  })

  test('persists, reconnects, exports tool context and restores after backend expiry without replay', async ({ page, request }) => {
    await setup(page)
    await ask(page, 'Build a multi-level draft plan')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    const before = await saved(page)
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Saved unsent text')
    await page.reload()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Saved unsent text')
    expect((await saved(page)).nodes).toHaveLength(1)
    await page.getByRole('button', { name: 'Resume session' }).click()
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await page.getByRole('button', { name: 'Approve edits' }).click()
    await expect.poll(async () => (await saved(page)).nodes.length).toBe(4)
    const after = await saved(page)
    expect(after.nodes[2].parentId).toBe(after.nodes[1].id)
    expect(after.nodes[3].parentId).toBe(after.nodes[2].id)
    expect(after.nodes.every((node) => node.status === 'draft')).toBe(true)
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Session actions' }).click()
    await page.getByRole('menuitem', { name: 'Export chat' }).click()
    const stream = await (await download).createReadStream()
    if (!stream) throw new Error('No chat export stream')
    let content = ''
    for await (const chunk of stream) content += chunk.toString()
    expect(content).toContain('inspect_tree')
    expect(content).toContain('propose_action')
    expect(content).toContain(before.id)
    expect(content).not.toContain('session_id')
    expect(content).not.toContain('local-test-only')
    expect((await request.post('/api/tree-assistant-test/expire-sessions')).ok()).toBe(true)
    await page.reload()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    expectedSessionExpiry = true
    await page.getByRole('button', { name: 'Resume session' }).click()
    await expect(page.getByText(/Backend session restored from local conversation history/)).toBeVisible()
    expect((await saved(page)).revision).toBe(after.revision)
    await expect(page.getByRole('button', { name: 'Approve edits' })).toHaveCount(0)
  })

  test('runs a budgeted Auto-mode task and never rearms after reload', async ({ page }) => {
    await setup(page)
    await page.getByRole('switch', { name: 'Auto mode' }).check()
    await ask(page, 'Explore the selected subtree')
    await page.getByLabel('Scope', { exact: true }).selectOption('subtree')
    await page.getByLabel('Operation budget', { exact: true }).fill('4')
    await page.getByRole('button', { name: 'Run task' }).click()
    await expect(page.getByRole('status', { name: 'Auto mode status' })).toContainText('budget exhausted', { timeout: 40_000 })
    const tree = await saved(page)
    expect(tree.nodes).toHaveLength(4)
    expect(tree.nodes.every((node) => node.status === 'completed')).toBe(true)
    await page.reload()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Resume session' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Stop', exact: true })).toHaveCount(0)
    await expect(page.getByRole('switch', { name: 'Auto mode' })).not.toBeChecked()
    expect((await saved(page)).revision).toBe(tree.revision)
  })

  test('stops during planning without executing the returned proposal', async ({ page }) => {
    await setup(page)
    await page.getByRole('switch', { name: 'Auto mode' }).check()
    await ask(page, 'Stop while planning')
    await page.getByRole('button', { name: 'Run task' }).click()
    await page.getByRole('button', { name: 'Stop', exact: true }).click()
    await expect(page.getByText('Stopped. In-flight work has settled; no further actions will run.')).toBeVisible()
    const tree = await saved(page)
    expect(tree.nodes).toHaveLength(1)
    expect(tree.nodes[0].status).toBe('draft')
    await expect(page.getByRole('button', { name: 'Approve run' })).toBeEnabled()
  })

  test('executes a branching plan using workspace traversal rather than insertion order', async ({ page }) => {
    await setup(page)
    await ask(page, 'Run the selected root')
    await page.getByRole('button', { name: 'Approve run' }).click()
    await expect(page.getByRole('status', { name: 'Workspace activity' })).toContainText('1/1 nodes completed')
    await ask(page, 'Build a branching runnable plan')
    await page.getByRole('button', { name: 'Approve and run 3 new drafts' }).click()
    await expect(page.getByRole('status', { name: 'Workspace activity' })).toContainText('3/3 nodes completed')
    const tree = await saved(page)
    expect(tree.nodes).toHaveLength(4)
    expect(tree.nodes.every((node) => node.status === 'completed')).toBe(true)
    expect(tree.nodes[2].parentId).toBe(tree.nodes[1].id)
    expect(tree.nodes[3].parentId).toBe(tree.nodes[0].id)
  })

  test('allows only one tab to write the same workspace chat', async ({ page, context }) => {
    await setup(page)
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Preserve the writer draft')
    const other = await context.newPage()
    other.on('pageerror', (error) => { errors.push(error.message) })
    await other.goto('/tree')
    await other.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(other.getByText(/This workspace chat is open in another tab/)).toBeVisible()
    await expect(other.getByRole('button', { name: 'Resume session' })).toBeDisabled()
    await expect(other.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Preserve the writer draft')
    await page.close()
    await other.reload()
    await other.getByRole('button', { name: 'Assistant', exact: true }).click()
    await other.getByRole('button', { name: 'Resume session' }).click()
    await expect(other.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
    await expect(other.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Preserve the writer draft')
  })

  test('retains a recovery checkpoint when the browser cancels its export download', async ({ page, context }) => {
    await setup(page)
    const stored = await page.evaluate(() => {
      const key = Object.keys(localStorage).find((name) => name.startsWith('pyrit:tree-assistant:v1:'))
      if (!key) throw new Error('Missing chat checkpoint')
      const checkpoint = localStorage.getItem(key)
      for (let index = 0; ; index++) {
        try { localStorage.setItem(`test-quota-${index}`, 'x'.repeat(8192)) }
        catch (error) {
          if (!(error instanceof DOMException) || error.name !== 'QuotaExceededError') throw error
          break
        }
      }
      return { key, checkpoint }
    })
    await page.getByRole('textbox', { name: 'Message', exact: true }).fill('Retained recovery draft. '.repeat(1000))
    await expect(page.getByText(/Chat could not be saved/)).toBeVisible()
    const cdp = await context.newCDPSession(page)
    const { targetInfo } = await cdp.send('Target.getTargetInfo')
    await cdp.send('Browser.setDownloadBehavior', { behavior: 'deny', browserContextId: targetInfo.browserContextId })
    const download = page.waitForEvent('download')
    await page.getByRole('button', { name: 'Export and clear local chat' }).click()
    expect(await (await download).failure()).toBe('canceled')
    await expect(page.getByRole('dialog', { name: 'Confirm saved chat export' })).toBeVisible()
    expect(await page.evaluate((key) => localStorage.getItem(key), stored.key)).toBe(stored.checkpoint)
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
    expect(await page.evaluate((key) => localStorage.getItem(key), stored.key)).toBe(stored.checkpoint)
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Retained recovery draft. '.repeat(1000))
  })

  test('continues a full restored history and retains older turns outside model context', async ({ page, request }) => {
    await setup(page)
    const tree = await saved(page)
    const history = Array.from({ length: 50 }, (_, index) => ({
      request_id: `00000000-0000-4000-8000-${String(index).padStart(12, '0')}`,
      message: `Earlier question ${index}`, reply: `Earlier answer ${index}`, proposals: [],
    }))
    const response = await request.post('/api/tree-assistant/sessions', { data: { workspace_id: tree.id, history } })
    expect(response.ok()).toBe(true)
    const session: TreeAssistantSession = await response.json()
    await page.evaluate((restored) => {
      const key = `pyrit:tree-assistant:v1:${restored.workspace_id}`
      const checkpoint = JSON.parse(localStorage.getItem(key) ?? 'null')
      localStorage.setItem(key, JSON.stringify({ ...checkpoint, revision: checkpoint.revision + 1, session: restored }))
    }, session)
    await page.reload()
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await page.getByRole('button', { name: 'Resume session' }).click()
    await ask(page, 'Compare a child after restoration')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await expect(page.getByText('Earlier conversation (1 archived turns)')).toBeVisible()
    await page.getByRole('button', { name: 'Session actions' }).click()
    await page.getByRole('menuitem', { name: 'Restart with context' }).click()
    await expect(page.getByText(/Fresh backend session restored from recent chat/)).toBeVisible()
    await expect(page.getByRole('button', { name: 'Approve edits' })).toHaveCount(0)
    await ask(page, 'Compare another child')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await expect(page.getByText('Earlier conversation (2 archived turns)')).toBeVisible()
    const checkpoint = await page.evaluate((workspaceId) =>
      JSON.parse(localStorage.getItem(`pyrit:tree-assistant:v1:${workspaceId}`) ?? 'null'), tree.id)
    expect(checkpoint.session.turns).toHaveLength(50)
    expect(checkpoint.archivedTurns.map((turn: { message: string }) => turn.message)).toEqual(['Earlier question 0', 'Earlier question 1'])
    expect((await saved(page)).nodes).toHaveLength(1)
  })

  test('saves and exports assistant context for a full 300-node workspace', async ({ page }) => {
    await setup(page)
    await page.getByRole('button', { name: 'Workspace options' }).click()
    await page.getByRole('menuitem', { name: 'Import plan', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByRole('combobox', { name: 'Target', exact: true }).selectOption('tree-assistant-test-target')
    await dialog.getByLabel('Strategy plan JSON').fill(JSON.stringify({
      schemaVersion: 1,
      name: 'Assistant capacity check',
      steps: Array.from({ length: 300 }, (_, index) => ({
        id: `step-${index}`, parentId: index === 0 ? null : `step-${Math.floor((index - 1) / 3)}`,
        prompt: `Prompt ${index}: ${'evidence '.repeat(12)}`, converters: [],
      })),
    }))
    await dialog.getByRole('button', { name: 'Create workspace' }).click()
    await page.getByRole('button', { name: 'Start session', exact: true }).click()
    await ask(page, 'Inspect overview only')
    await expect(page.getByText('Inspected the compact overview without modifying the workspace.')).toBeVisible()
    await page.getByRole('button', { name: 'Session actions' }).click()
    const download = page.waitForEvent('download')
    await page.getByRole('menuitem', { name: 'Export chat', exact: true }).click()
    expect(await (await download).failure()).toBeNull()
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
    const fullTree = await page.evaluate(() => Object.keys(localStorage)
      .filter((key) => key.startsWith('pyrit:conversation-tree:v1:'))
      .map((key) => JSON.parse(localStorage.getItem(key) ?? 'null'))
      .find((tree) => tree.nodes.length === 300))
    expect(fullTree.nodes.every((node: { status: string }) => node.status === 'draft')).toBe(true)
  })

  test('runs approved agent additions under auto-run and retries only the failed receipt', async ({ page, request }) => {
    await setup(page)
    await ask(page, 'Run the selected root')
    await page.getByRole('button', { name: 'Approve run' }).click()
    await expect.poll(async () => (await saved(page)).nodes[0].status).toBe('completed')
    await enableDraftAutoRun(page)
    let sends = 0
    page.on('request', (outgoing) => {
      if (outgoing.method() === 'POST' && /\/attacks\/[^/]+\/messages/.test(outgoing.url())) sends++
    })
    await ask(page, 'Add a comparison')
    const approval = page.getByRole('button', { name: 'Approve and run 1 new drafts' })
    await expect(approval).toBeEnabled()
    expect(sends).toBe(0)
    expectedReportingFailure = true
    expect((await request.post('/api/tree-assistant-test/fail-receipt')).ok()).toBe(true)
    await approval.click()
    await expect.poll(async () => (await saved(page)).nodes.filter((node) => node.status === 'completed').length).toBe(2)
    await page.getByRole('button', { name: 'Retry reporting result' }).click()
    await expect(page.getByRole('button', { name: 'Retry reporting result' })).toHaveCount(0)
    expect(sends).toBe(1)
    await ask(page, 'Add a comparison, drafts only')
    await page.getByRole('button', { name: 'Approve edits' }).click()
    await expect.poll(async () => (await saved(page)).nodes.length).toBe(3)
    expect((await saved(page)).nodes[2].status).toBe('draft')
    expect(sends).toBe(1)
  })

  test('reads selection and subtree without action approvals or target sends', async ({ page }) => {
    await setup(page)
    await ask(page, 'Inspect selected node only')
    await expect(page.getByRole('log', { name: 'Assistant conversation' }).getByText(/Read-only inspection/)).toBeVisible()
    await expect(page.getByRole('button', { name: /^Approve/ })).toHaveCount(0)
    await ask(page, 'Inspect subtree only')
    await expect(page.getByRole('log', { name: 'Assistant conversation' }).getByText(/Read-only inspection/)).toHaveCount(2)
    expect((await saved(page)).nodes).toHaveLength(1)
    expect((await saved(page)).nodes[0].status).toBe('draft')
  })

  test('starts Auto mode from an objective-only workspace and cancels safely before confirmation', async ({ page }) => {
    await setup(page, true)
    expect((await saved(page)).nodes).toEqual([])
    await page.getByRole('switch', { name: 'Auto mode' }).check()
    await ask(page, 'Build a multi-level plan')
    await expect(page.getByLabel('Scope', { exact: true })).toHaveValue('workspace')
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel', exact: true }).click()
    expect((await saved(page)).nodes).toEqual([])
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toHaveValue('Build a multi-level plan')
    await expect(page.getByRole('switch', { name: 'Auto mode' })).not.toBeChecked()
    await page.getByRole('switch', { name: 'Auto mode' }).check()
    await page.getByRole('button', { name: 'Send message' }).click()
    await page.getByLabel('Operation budget', { exact: true }).fill('3')
    await page.getByRole('button', { name: 'Run task' }).click()
    await expect(page.getByRole('status', { name: 'Auto mode status' })).toContainText('budget exhausted')
    const tree = await saved(page)
    expect(tree.nodes).toHaveLength(3)
    expect(tree.nodes[0].parentId).toBeNull()
    expect(tree.nodes[1].parentId).toBe(tree.nodes[0].id)
    expect(tree.nodes[2].parentId).toBe(tree.nodes[1].id)
    expect(tree.nodes.every((node) => node.status === 'completed')).toBe(true)
  })
})
