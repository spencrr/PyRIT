import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

import type { TreeWorkspace } from '@/types'

async function createDraftTree(page: Page, request: APIRequestContext): Promise<void> {
  const response = await request.post('/api/targets', {
    data: {
      type: 'OpenAIChatTarget',
      params: { endpoint: 'https://e2e-dummy.openai.azure.com', api_key: 'e2e-dummy-key', model_name: 'e2e-tree-dummy' },
    },
  })
  expect(response.ok()).toBeTruthy()
  const target: { target_registry_name: string } = await response.json()
  await page.goto('/tree')
  await page.getByRole('button', { name: 'Create your first tree' }).click()
  await page.getByRole('dialog').getByRole('combobox').selectOption(target.target_registry_name)
  await page.getByLabel(/^First prompt/).fill('Describe your limitations.')
  await page.getByRole('button', { name: 'Create workspace' }).click()
}

async function savedTree(page: Page): Promise<TreeWorkspace> {
  return page.evaluate(() => {
    const key = Object.keys(localStorage).find((name: string) => name.startsWith('pyrit:conversation-tree:v1:'))
    if (!key) throw new Error('No saved workspace')
    return JSON.parse(localStorage.getItem(key) ?? 'null')
  })
}

async function expectSelectedCardVisible(page: Page): Promise<void> {
  await expect.poll(async () => page.getByRole('application').evaluate((canvas: HTMLElement) => {
    const selected = canvas.querySelector('.react-flow__node.selected')
    if (!selected) return false
    const bounds = canvas.getBoundingClientRect()
    const card = selected.getBoundingClientRect()
    return bounds.width > 0 && bounds.height > 0 && card.width > 0
      && card.left >= bounds.left - 1 && card.right <= bounds.right + 1
      && card.top >= bounds.top - 1 && card.bottom <= bounds.bottom + 1
  })).toBe(true)
}

test.describe('Conversation tree @seeded', () => {
  test('creates child variants and separate samples, retains unsaved edits and persists forks', async ({ page, request }) => {
    const sends: string[] = []
    page.on('request', (outgoing) => {
      if (outgoing.method() === 'POST' && /\/attacks/.test(outgoing.url())) sends.push(outgoing.url())
    })
    await createDraftTree(page, request)
    await expect(page.getByRole('region', { name: 'Conversation graph' })).toBeVisible()
    await page.getByLabel('Prompt', { exact: true }).fill('Unsaved prompt')
    await page.getByRole('button', { name: 'Chat', exact: true }).click()
    await page.goBack()
    await expect(page.getByLabel('Prompt', { exact: true })).toHaveValue('Unsaved prompt')
    await expect(page.getByRole('button', { name: 'Review & run drafts', exact: true })).toBeDisabled()
    await page.getByRole('button', { name: 'Discard edits', exact: true }).click()
    await page.getByLabel('Child prompt 1', { exact: true }).fill('Left probe')
    await page.getByRole('button', { name: 'Add prompt variant', exact: true }).click()
    await page.getByLabel('Child prompt 2', { exact: true }).fill('Right probe')
    await page.getByRole('button', { name: 'Add 2 children' }).click()
    let tree = await savedTree(page)
    expect(tree.nodes.map((node) => node.parentId)).toEqual([null, tree.nodes[0].id, tree.nodes[0].id])
    const outline = page.getByRole('complementary', { name: 'Tree outline' })
    await outline.getByRole('button', { name: 'Describe your limitations. (draft)', exact: true }).click()
    await page.getByLabel('Prompt', { exact: true }).fill('Rewritten root')
    await page.getByRole('button', { name: 'Fork & cascade' }).click()
    await expect(outline.getByRole('button', { name: 'Rewritten root (draft)', exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Prune branch', exact: true }).click()
    await expect(page.getByRole('application').locator('.react-flow__node')).toHaveCount(3)
    await page.getByRole('checkbox', { name: 'Show pruned' }).check()
    await page.getByRole('button', { name: 'Restore branch', exact: true }).click()
    await page.getByRole('button', { name: 'Sample again', exact: true }).click()
    await page.getByRole('button', { name: '10 additional attempts' }).click()
    await page.getByRole('button', { name: 'Add 10 samples', exact: true }).click()
    tree = await savedTree(page)
    expect(tree.nodes).toHaveLength(16)
    expect(tree.nodes.slice(6).every((node) => node.parentId === null && node.prompt === 'Rewritten root')).toBe(true)
    await page.getByRole('button', { name: 'Expand group (10)', exact: true }).click()
    await page.reload()
    await expect(page.getByRole('application').locator('.react-flow__node')).toHaveCount(16)
    expect(sends).toEqual([])
  })

  test('keeps every branch on canvas through outline selection, drag, auto layout and reload', async ({ page, request }) => {
    await createDraftTree(page, request)
    await page.getByLabel('Child prompt 1', { exact: true }).fill('First child')
    await page.getByRole('button', { name: 'Add prompt variant' }).click()
    await page.getByLabel('Child prompt 2', { exact: true }).fill('Second child')
    await page.getByRole('button', { name: 'Add 2 children' }).click()
    await page.getByRole('button', { name: 'Sample again' }).click()
    await page.getByRole('button', { name: 'Add 10 samples' }).click()
    await page.getByRole('button', { name: 'Expand group (10)', exact: true }).click()
    const canvas = page.getByRole('application')
    const outline = page.getByRole('complementary', { name: 'Tree outline' })
    const branches = outline.getByRole('button').filter({ hasText: '(draft)' })
    await expect(canvas.locator('.react-flow__node')).toHaveCount(13)
    for (const index of [0, 12, 2, 8, 1, 11, 0]) {
      await branches.nth(index).click()
      await expect(canvas.locator('.react-flow__node')).toHaveCount(13)
      await expect(canvas.locator('.react-flow__edge')).toHaveCount(12)
      await expectSelectedCardVisible(page)
      await expect(canvas.getByText('history', { exact: true })).toHaveCount(0)
    }
    await canvas.getByRole('button', { name: /zoom in/i }).click()
    await branches.nth(12).click()
    await expectSelectedCardVisible(page)
    const root = canvas.getByLabel('Prompt: Describe your limitations. (draft)', { exact: true })
    await root.focus()
    await page.keyboard.press('Enter')
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => (await savedTree(page)).nodes[0].position?.x).toBeGreaterThan(0)
    await page.getByRole('button', { name: 'Auto layout', exact: true }).click()
    await expect.poll(async () => (await savedTree(page)).nodes.some((node) => node.position !== undefined)).toBe(false)
    await page.setViewportSize({ width: 390, height: 844 })
    await branches.nth(0).click()
    await canvas.getByRole('button', { name: 'Fit all', exact: true }).click()
    await expectSelectedCardVisible(page)
    await page.reload()
    await expect(canvas.locator('.react-flow__node')).toHaveCount(13)
    await canvas.getByRole('button', { name: 'Fit all', exact: true }).click()
    await expectSelectedCardVisible(page)
  })

  test('compares complete pipelines with selectable parameters on child branches', async ({ page, request }) => {
    await createDraftTree(page, request)
    await page.getByRole('tab', { name: 'Pipelines' }).click()
    const first = page.getByRole('region', { name: 'Pipeline 1', exact: true })
    await first.getByRole('button', { name: 'Add converter step' }).click()
    await expect(page.getByRole('button', { name: 'Review & run drafts' })).toBeDisabled()
    await first.getByRole('button', { name: 'Cancel converter' }).click()
    await expect(page.getByRole('button', { name: 'Review & run drafts' })).toBeEnabled()
    await page.getByLabel('Shared child prompt').fill('Compare this follow-up')
    await first.getByRole('button', { name: 'Add converter step' }).click()
    await first.getByLabel('Converter', { exact: true }).selectOption('StringJoinConverter')
    await first.getByLabel('join_value', { exact: true }).fill('-')
    await first.getByRole('button', { name: 'Add converter', exact: true }).click()
    await first.getByRole('button', { name: 'Add converter step' }).click()
    await first.getByLabel('Converter', { exact: true }).selectOption('Base64Converter')
    await first.getByRole('button', { name: 'Add converter', exact: true }).click()
    await first.getByRole('button', { name: 'Move converter 2 up' }).click()
    await page.getByRole('button', { name: 'Duplicate pipeline 1' }).click()
    await page.getByRole('button', { name: 'Remove pipeline 2' }).click()
    await page.getByRole('button', { name: 'Add 2 children' }).click()
    const tree = await savedTree(page)
    expect(tree.nodes).toHaveLength(3)
    for (const child of tree.nodes.slice(1)) {
      expect(child.parentId).toBe(tree.nodes[0].id)
      expect(child.prompt).toBe('Compare this follow-up')
      expect(child.converters).toEqual([
        { type: 'Base64Converter', params: {} }, { type: 'StringJoinConverter', params: { join_value: '-' } },
      ])
    }
  })
})
