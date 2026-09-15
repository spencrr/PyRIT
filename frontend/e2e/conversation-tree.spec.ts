import { test, expect, type Page, type APIRequestContext } from '@playwright/test'

import type { ConversationMessagesResponse, CreateAttackResponse, TreeWorkspace } from '@/types'

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
  test('creates child variants and original-inclusive sample groups, retains edits and persists forks', async ({ page, request }) => {
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
    expect(tree.groups?.find((group) => group.kind === 'sample')?.nodeIds).toHaveLength(11)
    const overview = await page.locator('.react-flow__viewport').getAttribute('style')
    await page.getByRole('button', { name: 'Focus group (11)', exact: true }).click()
    await expect(page.getByRole('application').locator('.react-flow__node')).toHaveCount(13)
    await page.getByRole('button', { name: 'Back to workspace' }).click()
    await expect(page.locator('.react-flow__viewport')).toHaveAttribute('style', overview ?? '')
    await page.getByRole('button', { name: 'Next stack member', exact: true }).click()
    await page.getByRole('button', { name: 'Next stack member', exact: true }).click()
    await page.getByRole('button', { name: 'Expand & auto layout (11)', exact: true }).click()
    await expect.poll(() => page.getByRole('application').locator('.react-flow__node').evaluateAll((nodes) => {
      const positions = nodes.map((node) => getComputedStyle(node).transform)
      return new Set(positions).size
    })).toBe(16)
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
    await page.getByRole('button', { name: 'Expand & auto layout (11)', exact: true }).click()
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

  test('supports resized previews, curved edges and reversible local edits without layout churn', async ({ page, request }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => { errors.push(error.message) })
    page.on('console', (message) => { if (/ResizeObserver loop|getNodesBounds/.test(message.text())) errors.push(message.text()) })
    await page.addInitScript(() => {
      window.addEventListener('error', (event) => { console.error(event.message) })
    })
    await createDraftTree(page, request)
    const canvas = page.getByRole('application')
    await expect(canvas.getByText('No scorer')).toHaveCount(0)
    await page.getByLabel('Node size', { exact: true }).selectOption('expanded')
    await expect.poll(async () => (await savedTree(page)).nodes[0].size).toEqual({ width: 480, height: 440 })
    await canvas.getByRole('button', { name: 'Focus selected', exact: true }).click()
    await page.evaluate(() => {
      const original = Storage.prototype.setItem
      sessionStorage.setItem('resize-writes', '0')
      Storage.prototype.setItem = function (key: string, value: string) {
        if (this === localStorage && key.startsWith('pyrit:conversation-tree:v1:')) {
          sessionStorage.setItem('resize-writes', String(Number(sessionStorage.getItem('resize-writes')) + 1))
        }
        original.call(this, key, value)
      }
    })
    const handle = canvas.locator('.react-flow__node.selected .react-flow__resize-control.handle.bottom.right')
    const box = await handle.boundingBox()
    expect(box).not.toBeNull()
    if (!box) throw new Error('Resize handle is not visible')
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2)
    await page.mouse.down()
    await page.mouse.move(box.x + box.width / 2 + 50, box.y + box.height / 2 + 40, { steps: 12 })
    expect(await page.evaluate(() => sessionStorage.getItem('resize-writes'))).toBe('0')
    await page.mouse.up()
    await expect.poll(() => page.evaluate(() => sessionStorage.getItem('resize-writes'))).toBe('1')
    const resized = (await savedTree(page)).nodes[0].size
    expect(resized?.width).toBeGreaterThan(480)
    await page.getByLabel('Prompt', { exact: true }).fill('Expanded local edit')
    await page.getByRole('button', { name: 'Save draft', exact: true }).click()
    await page.getByRole('button', { name: 'Undo edit', exact: true }).click()
    await expect(page.getByLabel('Prompt', { exact: true })).toHaveValue('Describe your limitations.')
    await page.getByRole('button', { name: 'Redo edit', exact: true }).click()
    await expect(page.getByLabel('Prompt', { exact: true })).toHaveValue('Expanded local edit')
    await page.getByLabel('Child prompt 1', { exact: true }).fill('Follow up')
    await page.getByRole('button', { name: 'Add 1 child', exact: true }).click()
    await expect(canvas.locator('.react-flow__edge-default')).toHaveCount(1)
    await page.getByRole('button', { name: 'Prune branch', exact: true }).click()
    await page.getByRole('checkbox', { name: 'Show pruned' }).check()
    await expect(canvas.getByText('Pruned', { exact: true })).toHaveCount(1)
    await page.reload()
    await expect.poll(async () => (await savedTree(page)).nodes[0].size).toEqual(resized)
    expect(errors).toEqual([])
  })

  test('imports appended backend exchanges exactly once without sending target requests', async ({ page, request }) => {
    await createDraftTree(page, request)
    const tree = await savedTree(page)
    const created = await request.post('/api/attacks', { data: { target_registry_name: tree.targetRegistryName } })
    expect(created.ok()).toBeTruthy()
    const attack: CreateAttackResponse = await created.json()
    async function store(role: string, text: string): Promise<void> {
      const response = await request.post(`/api/attacks/${attack.attack_result_id}/messages`, {
        data: { role, pieces: [{ data_type: 'text', original_value: text }], send: false, target_conversation_id: attack.conversation_id },
      })
      expect(response.ok()).toBeTruthy()
    }
    await store('user', tree.nodes[0].prompt)
    await store('assistant', 'Original recorded response')
    const messageResponse = await request.get(`/api/attacks/${attack.attack_result_id}/messages`, { params: { conversation_id: attack.conversation_id } })
    const messages: ConversationMessagesResponse = await messageResponse.json()
    messages.messages = messages.messages.map((message) => ({
      role: message.role, turn_number: message.turn_number, created_at: message.created_at,
      message_pieces: message.message_pieces.map((piece) => ({
        id: piece.id, original_value_data_type: piece.original_value_data_type,
        converted_value_data_type: piece.converted_value_data_type, original_value: piece.original_value,
        converted_value: piece.converted_value, response_error: piece.response_error, scores: [],
      })),
    }))
    tree.nodes[0] = { ...tree.nodes[0], status: 'completed', attackResultId: attack.attack_result_id, conversationId: attack.conversation_id,
      attemptId: crypto.randomUUID(), lastSequence: messages.messages[messages.messages.length - 1].turn_number, messages: messages.messages }
    await page.evaluate((workspace: TreeWorkspace) => { localStorage.setItem(`pyrit:conversation-tree:v1:${workspace.id}`, JSON.stringify(workspace)) }, tree)
    await page.reload()
    await store('user', 'Appended from backend conversation')
    await store('assistant', 'Appended recorded response')
    await store('user', 'Still awaiting a response')
    const sends: string[] = []
    page.on('request', (outgoing) => { if (outgoing.method() === 'POST' && /\/attacks/.test(outgoing.url())) sends.push(outgoing.url()) })
    await page.getByRole('button', { name: 'Check backend history' }).click()
    await expect(page.getByText(/1 new complete turns; 1 pending messages/)).toBeVisible()
    await page.getByRole('button', { name: 'Import continuation' }).click()
    await expect.poll(async () => (await savedTree(page)).nodes.length).toBe(2)
    await expect(page.getByLabel('Prompt', { exact: true })).toHaveValue('Appended from backend conversation')
    await page.getByRole('button', { name: 'Check backend history' }).click()
    await expect(page.getByRole('button', { name: 'Import continuation' })).toBeDisabled()
    expect((await savedTree(page)).nodes[1].importedFromBackend).toBe(true)
    expect(sends).toEqual([])
  })

  test('constructs nested scorers and YAML SeedPrompts with side-effect-free preflight', async ({ page, request }) => {
    await createDraftTree(page, request)
    const tree = await savedTree(page)
    await page.getByRole('button', { name: 'Scoring', exact: true }).click()
    await page.getByText('Configure a new scorer', { exact: true }).click()
    await page.getByLabel('Scorer type', { exact: true }).selectOption('TrueFalseInverterScorer')
    await page.getByLabel('scorer source', { exact: true }).selectOption({ label: 'Configure nested scorer' })
    await page.getByLabel(/^scorer scorer type/).selectOption('RegexScorer')
    await page.getByRole('textbox', { name: 'patterns', exact: true }).fill('{"match":"harmless"}')
    const before = (await (await request.get('/api/scorers')).json()).items.length
    await page.getByRole('button', { name: 'Validate configuration', exact: true }).click()
    await expect(page.getByText('Configuration is valid.', { exact: true })).toBeVisible()
    expect((await (await request.get('/api/scorers')).json()).items.length).toBe(before)
    await page.getByRole('button', { name: 'Create and select scorer', exact: true }).click()
    await expect(page.getByText('TrueFalseInverterScorer is ready to use.', { exact: true })).toBeVisible()
    expect((await (await request.get('/api/scorers')).json()).items.length).toBe(before + 1)

    await page.getByLabel('Scorer type', { exact: true }).selectOption('SelfAskScaleScorer')
    await page.getByLabel('Judge target', { exact: true }).selectOption(tree.targetRegistryName)
    await page.getByLabel('system_prompt format', { exact: true }).selectOption({ label: 'YAML' })
    await page.getByRole('textbox', { name: 'system_prompt', exact: true }).fill(
      'value: Judge this harmless response\ndata_type: text\nname: local-seed\nmetadata:\n  purpose: browser-validation',
    )
    await page.getByRole('textbox', { name: 'scale', exact: true }).fill('{"minimum_value":0,"maximum_value":10,"category":"math"}')
    await page.getByRole('button', { name: 'Validate configuration', exact: true }).click()
    await expect(page.getByText('Configuration is valid.', { exact: true })).toBeVisible()
    expect((await (await request.get('/api/scorers')).json()).items.length).toBe(before + 1)
    await page.getByRole('button', { name: 'Create and select scorer', exact: true }).click()
    await expect(page.getByText('SelfAskScaleScorer is ready to use.', { exact: true })).toBeVisible()
    await page.getByRole('button', { name: 'Save scoring settings', exact: true }).click()
    expect((await savedTree(page)).settings?.scorers.map((scorer) => scorer.scorer_type))
      .toEqual(['TrueFalseInverterScorer', 'SelfAskScaleScorer'])
  })

  test('starts a new workspace outside the previous focused group and edit history', async ({ page, request }) => {
    await createDraftTree(page, request)
    const original = await savedTree(page)
    await page.getByLabel('Node size', { exact: true }).selectOption('expanded')
    await page.getByRole('button', { name: 'Sample again', exact: true }).click()
    await page.getByRole('button', { name: 'Add 10 samples', exact: true }).click()
    await page.getByRole('button', { name: 'Focus group (11)', exact: true }).click()
    await expect(page.getByRole('button', { name: 'Back to workspace' })).toBeVisible()
    await page.getByRole('button', { name: 'New tree', exact: true }).click()
    await page.getByRole('dialog').getByRole('combobox').selectOption(original.targetRegistryName)
    await page.getByLabel(/^First prompt/).fill('Separate workspace')
    await page.getByRole('button', { name: 'Create workspace' }).click()
    await expect(page.getByRole('button', { name: 'Back to workspace' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Undo edit', exact: true })).toBeDisabled()
    const workspaceId = await page.getByRole('combobox', { name: 'Workspace', exact: true }).inputValue()
    expect(workspaceId).not.toBe(original.id)
    const root = page.getByRole('application').getByLabel('Prompt: Separate workspace (draft)', { exact: true })
    await root.focus()
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => page.evaluate((id: string) => {
      const tree: TreeWorkspace = JSON.parse(localStorage.getItem(`pyrit:conversation-tree:v1:${id}`) ?? 'null')
      return tree.nodes[0].position?.x
    }, workspaceId)).toBeGreaterThan(0)
  })

  test('invalidates an inactive overview after auto layout inside a focused group', async ({ page, request }) => {
    await createDraftTree(page, request)
    await page.getByRole('button', { name: 'Sample again', exact: true }).click()
    await page.getByRole('button', { name: 'Add 10 samples', exact: true }).click()
    const root = page.getByRole('application').getByLabel('Prompt: Describe your limitations. (draft)', { exact: true })
    await root.focus()
    await page.keyboard.press('ArrowRight')
    await expect.poll(async () => (await savedTree(page)).nodes[0].position?.x).toBeGreaterThan(0)
    await page.getByRole('button', { name: 'Focus group (11)', exact: true }).click()
    await page.getByRole('button', { name: 'Auto layout', exact: true }).click()
    await expect.poll(async () => (await savedTree(page)).nodes.every((node) => node.position === undefined)).toBe(true)
    await page.getByRole('button', { name: 'Back to workspace', exact: true }).click()
    await expect.poll(async () => root.evaluate((node) => new DOMMatrixReadOnly(getComputedStyle(node).transform).m41)).toBe(0)
    await page.reload()
    await expect.poll(async () => root.evaluate((node) => new DOMMatrixReadOnly(getComputedStyle(node).transform).m41)).toBe(0)
  })
})
