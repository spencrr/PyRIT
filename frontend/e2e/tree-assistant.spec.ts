import { expect, test, type Page } from '@playwright/test'

import type { TreeWorkspace } from '@/types'

async function setup(page: Page): Promise<void> {
  await page.goto('/tree')
  await page.getByRole('button', { name: 'Create your first tree' }).click()
  await page.getByRole('dialog').getByRole('combobox').selectOption('tree-assistant-test-target')
  await page.getByLabel(/^First prompt/).fill('Describe your limitations.')
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

test.describe('Tree assistant @assistant', () => {
  let errors: string[] = []
  let expectedReportingFailure = false
  test.beforeEach(async ({ page }) => {
    errors = []
    expectedReportingFailure = false
    page.on('pageerror', (error) => { errors.push(error.message) })
    page.on('console', (event) => { if (event.type() === 'error') errors.push(event.text()) })
    await page.addInitScript(() => { window.addEventListener('error', (event) => { console.error(event.message) }) })
  })
  test.afterEach(() => {
    const unexpected = errors.filter((error) => !expectedReportingFailure || !(
      /^Failed to load resource: the server responded with a status of 503/.test(error)
      || /^\[API\] POST \/tree-assistant\/sessions\/.*\/result failed .*Receipt acknowledgement lost$/.test(error)
    ))
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
    await page.getByRole('complementary', { name: 'Tree outline' }).getByRole('button', { name: 'Describe your limitations. (draft)', exact: true }).click()
    await ask(page, 'Run the selected root')
    await expect(page.getByRole('button', { name: 'Approve run' })).toBeEnabled()
    expect(sends).toHaveLength(0)
    await page.getByRole('button', { name: 'Approve run' }).click()
    await expect(page.getByText(/1\/1 nodes completed/)).toBeVisible()
    expect((await saved(page)).nodes.map((node) => node.status)).toEqual(['completed', 'draft'])
    const sent = sends.length
    await ask(page, 'Inspect stored evidence')
    await expect(page.getByText(/stored_backend_evidence/)).toBeVisible()
    expect(sends).toHaveLength(sent)
    await expect(page.getByRole('textbox', { name: 'Message', exact: true })).toBeEnabled()
    expect(errors).toEqual([])
  })

  test('rejects stale edits and reports a completed action again without applying twice', async ({ page, request }) => {
    await setup(page)
    await ask(page, 'Add a comparison')
    await expect(page.getByRole('button', { name: 'Approve edits' })).toBeEnabled()
    await page.getByLabel('Node size', { exact: true }).selectOption('expanded')
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
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    await page.getByRole('button', { name: 'Assistant', exact: true }).click()
    await expect(page.getByRole('complementary', { name: 'Turn inspector' })).toBeVisible()
  })
})
