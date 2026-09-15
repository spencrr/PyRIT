import type { TreeWorkspace } from '@/types'

import { applyTreeCommand, createTreeWorkspace } from './treeModel'
import { deleteTreeWorkspace, listTreeWorkspaces, loadTreeWorkspace, saveTreeWorkspace } from './treeStorage'

const CONFIGURATION = {
  name: 'Tree', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
}

describe('treeStorage', () => {
  const originalLocks = Object.getOwnPropertyDescriptor(navigator, 'locks')
  let tail: Promise<unknown>
  let request: jest.Mock

  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
    tail = Promise.resolve()
    request = jest.fn((_name: string, _options: unknown, callback: () => unknown): Promise<unknown> => {
      const next = tail.then(callback)
      tail = next.catch(() => undefined)
      return next
    })
    Object.defineProperty(navigator, 'locks', { configurable: true, value: { request } })
  })

  afterEach(() => {
    jest.restoreAllMocks()
    if (originalLocks) Object.defineProperty(navigator, 'locks', originalLocks)
    else Reflect.deleteProperty(navigator, 'locks')
  })

  it('should persist and reload snapshots with revisions incremented only by saves', async () => {
    const original = createTreeWorkspace(CONFIGURATION)
    const saved = await saveTreeWorkspace(original)
    expect(original.revision).toBe(0)
    expect(saved.revision).toBe(1)
    expect(loadTreeWorkspace(saved.id)).toEqual(saved)
    expect(request).toHaveBeenCalledWith(expect.stringContaining(saved.id), { mode: 'exclusive' }, expect.any(Function))
    localStorage.setItem('unrelated-key', 'not JSON')
    expect(listTreeWorkspaces()).toEqual([saved])
    const edited = await saveTreeWorkspace(applyTreeCommand(saved, { type: 'add', parentId: null, prompt: 'hello' }))
    expect(edited.revision).toBe(2)
  })

  it('should detect cross-tab conflicting saves inside a serialized Web Lock', async () => {
    const saved = await saveTreeWorkspace(createTreeWorkspace(CONFIGURATION))
    const results = await Promise.allSettled([
      saveTreeWorkspace({ ...saved, name: 'Tab one' }),
      saveTreeWorkspace({ ...saved, name: 'Tab two' }),
    ])
    expect(results.map((result: PromiseSettledResult<TreeWorkspace>) => result.status)).toEqual(['fulfilled', 'rejected'])
    expect(loadTreeWorkspace(saved.id).name).toBe('Tab one')
    expect(loadTreeWorkspace(saved.id).revision).toBe(2)
  })

  it('should refuse unsafe persistence when Web Locks are unavailable', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: undefined })
    await expect(saveTreeWorkspace(createTreeWorkspace(CONFIGURATION))).rejects.toThrow('Web Locks')
    expect(localStorage.length).toBe(0)
  })

  it('should propagate quota failures without consuming a revision', async () => {
    const saved = await saveTreeWorkspace(createTreeWorkspace(CONFIGURATION))
    const setItem = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Quota exceeded') })
    await expect(saveTreeWorkspace(saved)).rejects.toThrow('Quota')
    setItem.mockRestore()
    expect(loadTreeWorkspace(saved.id).revision).toBe(1)
    expect((await saveTreeWorkspace(saved)).revision).toBe(2)
  })

  it('should fail explicitly on malformed records without deleting or rewriting them', async () => {
    const original = createTreeWorkspace(CONFIGURATION)
    const key = `pyrit:conversation-tree:v1:${original.id}`
    localStorage.setItem(key, '{broken')
    expect(() => listTreeWorkspaces()).toThrow('malformed')
    expect(() => loadTreeWorkspace(original.id)).toThrow('malformed')
    await expect(saveTreeWorkspace(original)).rejects.toThrow('malformed')
    await expect(deleteTreeWorkspace(original)).rejects.toThrow('malformed')
    expect(localStorage.getItem(key)).toBe('{broken')
  })

  it('should reject mismatching storage identity', () => {
    const original = createTreeWorkspace(CONFIGURATION)
    localStorage.setItem('pyrit:conversation-tree:v1:other', JSON.stringify(original))
    expect(() => listTreeWorkspaces()).toThrow('storage key')
  })

  it('should CAS deletion and prevent stale writes from recreating deleted records', async () => {
    const first = await saveTreeWorkspace(createTreeWorkspace(CONFIGURATION))
    const second = await saveTreeWorkspace(first)
    await expect(deleteTreeWorkspace(first)).rejects.toThrow('conflict')
    await deleteTreeWorkspace(second)
    expect(listTreeWorkspaces()).toEqual([])
    expect(() => loadTreeWorkspace(second.id)).toThrow('not found')
    await expect(saveTreeWorkspace(second)).rejects.toThrow('conflict')
    await expect(deleteTreeWorkspace(createTreeWorkspace(CONFIGURATION))).rejects.toThrow('not found')
  })

  it('should preserve interrupted states and forbid resets of observed evidence', async () => {
    const draft = applyTreeCommand(createTreeWorkspace(CONFIGURATION), { type: 'add', parentId: null, prompt: 'hello' })
    let saved = await saveTreeWorkspace(draft)
    saved = await saveTreeWorkspace({
      ...saved, nodes: [{ ...saved.nodes[0], status: 'running', attackResultId: 'attack', conversationId: 'conversation' }],
    })
    expect(loadTreeWorkspace(saved.id).nodes[0].status).toBe('running')
    await expect(saveTreeWorkspace({ ...saved, nodes: draft.nodes })).rejects.toThrow('cannot be reset')
    await expect(saveTreeWorkspace({ ...saved, targetIdentifierHash: 'changed' })).rejects.toThrow('configuration')
    saved = await saveTreeWorkspace({ ...saved, nodes: [{ ...saved.nodes[0], status: 'error', error: 'Inspect history' }] })
    await expect(saveTreeWorkspace({ ...saved, nodes: [] })).rejects.toThrow('cannot be removed')
    await expect(saveTreeWorkspace({ ...saved, nodes: [{ ...saved.nodes[0], prompt: 'rewrite' }] })).rejects.toThrow('immutable')
    const moved = await saveTreeWorkspace(applyTreeCommand(saved, { type: 'move', nodeId: saved.nodes[0].id, position: { x: 4, y: 5 } }))
    expect(moved.nodes[0].position).toEqual({ x: 4, y: 5 })
  })
})
