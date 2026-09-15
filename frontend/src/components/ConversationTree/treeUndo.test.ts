import { applyTreeCommand, createTreeWorkspace } from './treeModel'
import { captureTreeUndo, reverseTreeUndo } from './treeUndo'

function fixture() {
  return applyTreeCommand(createTreeWorkspace({
    name: 'Undo', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
  }), { type: 'add', parentId: null, prompt: 'Original' })
}

describe('treeUndo', () => {
  it('redos pruning when the command legitimately rehomes a sample stack', () => {
    const draft = fixture()
    const before = applyTreeCommand(draft, { type: 'sample', nodeId: draft.nodes[0].id, count: 2 })
    const after = applyTreeCommand(before, { type: 'prune', nodeId: before.nodes[0].id, pruned: true })
    const entry = captureTreeUndo(before, after)
    expect(after.groups?.[0].activeNodeId).not.toBe(before.nodes[0].id)
    const undone = reverseTreeUndo(after, entry, false)
    expect(undone.groups?.[0].activeNodeId).toBe(before.nodes[0].id)
    expect(reverseTreeUndo(undone, entry, true).groups).toEqual(after.groups)
  })
  it('reverses a draft edit and redoes it without discarding independent changes', () => {
    const before = fixture()
    const after = applyTreeCommand(before, { type: 'edit', nodeId: before.nodes[0].id, prompt: 'Edited', converters: [] })
    const entry = captureTreeUndo(before, after)
    const moved = applyTreeCommand(after, { type: 'move', nodeId: before.nodes[0].id, position: { x: 20, y: 90 } })
    const undone = reverseTreeUndo(moved, entry, false)
    expect(undone.nodes[0]).toMatchObject({ prompt: 'Original', position: { x: 20, y: 90 } })
    expect(reverseTreeUndo(undone, entry, true).nodes[0].prompt).toBe('Edited')
  })
  it('never overwrites subsequent prompt edits or new attempts', () => {
    const before = fixture()
    const after = applyTreeCommand(before, { type: 'edit', nodeId: before.nodes[0].id, prompt: 'Edited', converters: [] })
    const entry = captureTreeUndo(before, after)
    expect(() => reverseTreeUndo({ ...after, nodes: [{ ...after.nodes[0], prompt: 'New input' }] }, entry, false)).toThrow(/changed since/)
    expect(() => reverseTreeUndo({ ...after, nodes: [{ ...after.nodes[0], attemptId: 'new' }] }, entry, false)).toThrow(/new attempt/)
    expect(() => reverseTreeUndo({ ...after, id: 'different' }, entry, false)).toThrow(/another workspace/)
  })
  it('reverses pruning and resizing without clearing completed evidence', () => {
    const before = fixture()
    before.nodes[0] = { ...before.nodes[0], status: 'completed', attemptId: 'observed', attackResultId: 'attack', conversationId: 'conversation', lastSequence: 1,
      messages: ['user', 'assistant'].map((role, turn_number) => ({
        role, turn_number, created_at: '2026-01-01T00:00:00.000Z', message_pieces: [{
          id: `piece-${turn_number}`, original_value_data_type: 'text', converted_value_data_type: 'text',
          original_value: role === 'user' ? before.nodes[0].prompt : 'Evidence',
          converted_value: role === 'user' ? before.nodes[0].prompt : 'Evidence', response_error: 'none', scores: [],
        }],
      })),
    }
    const resized = applyTreeCommand(before, { type: 'resize', nodeId: before.nodes[0].id, size: { width: 500, height: 500 } })
    const after = applyTreeCommand(resized, { type: 'prune', nodeId: before.nodes[0].id, pruned: true })
    const undone = reverseTreeUndo(after, captureTreeUndo(before, after), false)
    expect(undone.nodes[0]).toMatchObject({ status: 'completed', messages: before.nodes[0].messages, attemptId: 'observed', pruned: false })
    expect(undone.nodes[0].size).toBeUndefined()
  })
})
