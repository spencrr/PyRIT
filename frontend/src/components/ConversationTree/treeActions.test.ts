import type { TreeNode, TreeWorkspace } from '@/types'

import { prepareTreeChange } from './treeActions'
import { applyTreeCommand, createTreeWorkspace, getCurrentAttemptId, getTreeSettings, parseTreeWorkspace } from './treeModel'
import { reverseTreeUndo } from './treeUndo'

function fixture(): TreeWorkspace {
  return parseTreeWorkspace(JSON.stringify({
    ...createTreeWorkspace({
      name: 'Actions', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
    }),
    nodes: ['root', 'other', 'child'].map((id: string): TreeNode => ({
      id, parentId: id === 'child' ? 'root' : null, prompt: id, converters: [], status: 'draft',
      pruned: false, kept: false, position: { x: 200, y: 200 },
    })),
  }))
}

describe('prepareTreeChange', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should prepare an atomic batch with a single reversible edit and no input mutation', () => {
    const before = fixture()
    const original = JSON.stringify(before)
    const change = prepareTreeChange(before, [
      { type: 'edit', nodeId: 'root', prompt: 'New prompt', converters: [] },
      { type: 'prune', nodeId: 'root', pruned: true },
      { type: 'move', nodeId: 'other', position: { x: 1, y: 2 } },
    ])
    expect(JSON.stringify(before)).toBe(original)
    expect(change.addedNodeIds).toEqual([])
    expect(change.affectedNodeIds).toEqual(['root', 'other', 'child'])
    expect(change.selectionId).toBeUndefined()
    expect(change.layoutChanged).toBe(false)
    expect(change.workspace.revision).toBe(before.revision)
    expect(change.undo).not.toBeNull()
    if (!change.undo) throw new Error('Expected an undo entry')
    expect(reverseTreeUndo(change.workspace, change.undo, false).nodes).toEqual(before.nodes)
    expect(() => prepareTreeChange(before, [
      { type: 'edit', nodeId: 'root', prompt: 'Valid', converters: [] },
      { type: 'edit', nodeId: 'missing', prompt: 'Invalid', converters: [] },
    ])).toThrow('node not found')
    expect(JSON.stringify(before)).toBe(original)
  })

  it('should preserve sample, fork, and retry selection without scheduling execution', () => {
    const before = fixture()
    const sample = prepareTreeChange(before, [{ type: 'sample', nodeId: 'root', count: 2 }])
    expect(sample.selectionId).toBe('root')
    expect(sample.addedNodeIds).toHaveLength(2)
    expect(sample.undo).toBeNull()
    const fork = prepareTreeChange(before, [{ type: 'fork', nodeId: 'root', prompt: 'Branch', converters: [] }])
    expect(fork.selectionId).toBe(fork.addedNodeIds[0])
    expect(fork.addedNodeIds).toHaveLength(2)
    const failed = { ...before, nodes: before.nodes.map((node: TreeNode): TreeNode =>
      node.id === 'root' ? { ...node, status: 'error', error: 'Provider failed' } : node) }
    const retry = prepareTreeChange(failed, [{ type: 'retry', nodeId: 'root', scope: 'subtree' }])
    expect(retry.selectionId).toBe('root')
    expect(retry.undo).toBeNull()
    expect(retry.workspace.nodes[0].status).toBe('draft')
    expect(getCurrentAttemptId(retry.workspace.nodes[0])).not.toBe(getCurrentAttemptId(failed.nodes[0]))
  })

  it('should auto-layout expanded groups and capture positions and collapse state together', () => {
    const before = applyTreeCommand(fixture(), { type: 'sample', nodeId: 'root', count: 2 })
    const group = before.groups?.[0]
    if (!group) throw new Error('Expected sample group')
    const expanded = prepareTreeChange(before, [{ type: 'group', groupId: group.id, collapsed: false }])
    expect(expanded.layoutChanged).toBe(true)
    expect(expanded.workspace.nodes.every((node: TreeNode) => node.position === undefined)).toBe(true)
    expect(expanded.workspace.groups?.[0].collapsed).toBe(false)
    if (!expanded.undo) throw new Error('Expected expansion undo')
    const undone = reverseTreeUndo(expanded.workspace, expanded.undo, false)
    expect(undone.groups).toEqual(before.groups)
    expect(undone.nodes).toEqual(before.nodes)
    const selected = prepareTreeChange(before, [{ type: 'group', groupId: group.id, activeNodeId: group.nodeIds[1] }])
    expect(selected.selectionId).toBe(group.nodeIds[1])
    expect(selected.layoutChanged).toBe(false)
    expect(selected.undo).toBeNull()
    expect(selected.affectedNodeIds).toEqual(expect.arrayContaining(group.nodeIds))
  })

  it('should not create partial undo entries for batches containing additions or settings', () => {
    const before = fixture()
    const change = prepareTreeChange(before, [
      { type: 'edit', nodeId: 'root', prompt: 'Edited', converters: [] },
      { type: 'add', parentId: null, prompt: 'Added' },
    ])
    expect(change.addedNodeIds).toHaveLength(1)
    expect(change.selectionId).toBe(change.addedNodeIds[0])
    expect(change.undo).toBeNull()
    const settings = prepareTreeChange(before, [{ type: 'settings', settings: { ...getTreeSettings(before), markdown: true } }])
    expect(settings.undo).toBeNull()
    expect(prepareTreeChange(before, []).undo).toBeNull()
    expect(prepareTreeChange(before, [{ type: 'edit', nodeId: 'root', prompt: 'root', converters: [] }]).undo).toBeNull()
  })
})
