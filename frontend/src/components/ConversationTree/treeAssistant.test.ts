import type { TreeAssistantAction, TreeAssistantProposal, TreeWorkspace } from '@/types'

import {
  applyAssistantReview, captureAssistantPrecondition, createAssistantContext, prepareAssistantProposal, previewAssistantProposal,
  rebasePreparedAssistantProposal, validateAutonomousProposal, validatePreparedAssistantProposal,
} from './treeAssistant'
import { applyTreeCommand, createTreeWorkspace, getTreeSettings } from './treeModel'
import { reverseTreeUndo } from './treeUndo'

function fixture(): TreeWorkspace {
  return applyTreeCommand(createTreeWorkspace({
    name: 'Assistant test', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: 'System',
    labels: { operator: 'private-label' },
  }), { type: 'add', parentId: null, prompt: 'Describe your limits.' })
}

function proposal(tree: TreeWorkspace, action: TreeAssistantAction): TreeAssistantProposal {
  return { id: 'proposal', workspace_id: tree.id, base_revision: tree.revision, summary: 'Explore a branch', status: 'pending', action }
}

function observed(tree: TreeWorkspace): TreeWorkspace {
  const node = tree.nodes[0]
  return { ...tree, nodes: [{ ...node, status: 'completed', attackResultId: 'attack', conversationId: 'conversation', lastSequence: 1,
    messages: ['user', 'assistant'].map((role, turn_number) => ({
      role, turn_number, created_at: '2026-09-17T00:00:00.000Z', message_pieces: [{
        id: `piece-${turn_number}`, original_value: role === 'user' ? node.prompt : 'Response',
        converted_value: role === 'user' ? node.prompt : 'Response', original_value_data_type: 'text',
        converted_value_data_type: 'text', scores: [], response_error: 'none',
      }],
    })),
  }] }
}

describe('treeAssistant', () => {
  it('rebases a captured mutation at the save boundary without regenerating allocated node or attempt IDs', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'mutate', commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Child' }] })
    const review = prepareAssistantProposal(tree, proposed)
    if (review.kind !== 'mutate') throw new Error('Expected mutation')
    const latest: TreeWorkspace = {
      ...tree, revision: tree.revision + 3, updatedAt: '2026-09-18T14:00:00Z',
      settings: { ...getTreeSettings(tree), markdown: false, nodeSize: 'expanded', edgeStyle: 'straight', stackSamples: false, stackVariants: false },
      nodes: tree.nodes.map((node) => ({ ...node, position: { x: 400, y: 250 }, size: { width: 420, height: 260 } })),
    }
    const applied = applyAssistantReview(latest, proposed, review)
    if (applied.kind !== 'mutate') throw new Error('Expected mutation')
    expect(applied.addedNodeIds).toEqual(review.addedNodeIds)
    expect(applied.workspace.nodes[1]).toEqual(review.workspace.nodes[1])
    expect(applied.workspace.nodes[0].position).toEqual(latest.nodes[0].position)
    expect(applied.workspace.nodes[0].size).toEqual(latest.nodes[0].size)
    expect(applied.workspace.settings).toEqual(latest.settings)
    expect(applied.workspace.revision).toBe(latest.revision)
    expect(applied.workspace.updatedAt).toBe(latest.updatedAt)
    expect(applied.change.workspace).toBe(applied.workspace)
    expect(applied.change.addedNodeIds).toEqual(review.addedNodeIds)
    expect(applied.precondition).toEqual(review.precondition)
    expect(Object.isFrozen(applied.change.workspace.nodes)).toBe(true)
    expect(review.workspace.nodes[0].position).toEqual(tree.nodes[0].position)
    expect(latest.nodes).toHaveLength(1)
  })

  it('rebases current group presentation and undo snapshots without reverting either when the edit is undone', () => {
    const original = fixture()
    const tree = applyTreeCommand(original, { type: 'sample', nodeId: original.nodes[0].id, count: 1 })
    const proposed = proposal(tree, { kind: 'mutate', commands: [{ type: 'edit', nodeId: tree.nodes[0].id, prompt: 'Reviewed edit', converters: [] }] })
    const review = prepareAssistantProposal(tree, proposed)
    const latest: TreeWorkspace = {
      ...tree, revision: tree.revision + 1,
      nodes: tree.nodes.map((node) => ({ ...node, position: { x: 450, y: 310 } })),
      groups: tree.groups?.map((group) => ({ ...group, collapsed: false, activeNodeId: tree.nodes[1].id })),
    }
    const applied = applyAssistantReview(latest, proposed, review)
    if (applied.kind !== 'mutate' || !applied.change.undo) throw new Error('Expected undoable mutation')
    expect(applied.workspace.groups).toEqual(latest.groups)
    expect(applied.change.undo.nodes[0].before.position).toEqual(latest.nodes[0].position)
    const undone = reverseTreeUndo(applied.workspace, applied.change.undo, false)
    expect(undone.nodes[0].prompt).toBe(tree.nodes[0].prompt)
    expect(undone.nodes[0].position).toEqual(latest.nodes[0].position)
    expect(undone.groups).toEqual(latest.groups)
  })

  it.each(['prune', 'keep'] as const)('does not resurrect a group member hidden by approved %s when rebasing newer paging', (commandType) => {
    const original = fixture()
    const tree = applyTreeCommand(original, { type: 'sample', nodeId: original.nodes[0].id, count: 2 })
    const root = tree.nodes[0].id
    const proposed = proposal(tree, { kind: 'mutate', commands: [
      commandType === 'prune' ? { type: 'prune', nodeId: root, pruned: true } : { type: 'keep', nodeId: root },
    ] })
    const review = prepareAssistantProposal(tree, proposed)
    const liveActiveId = commandType === 'prune' ? root : tree.nodes[1].id
    const latest: TreeWorkspace = {
      ...tree, revision: tree.revision + 1,
      nodes: tree.nodes.map((node) => ({ ...node, position: { x: 500, y: 350 } })),
      groups: tree.groups?.map((group) => ({ ...group, collapsed: false, activeNodeId: liveActiveId })),
    }
    const applied = rebasePreparedAssistantProposal(latest, proposed, review)
    if (applied.kind !== 'mutate' || !applied.change.undo) throw new Error('Expected undoable mutation')
    const activeId = applied.workspace.groups?.[0].activeNodeId
    expect(activeId).not.toBe(liveActiveId)
    expect(applied.workspace.nodes.find((node) => node.id === activeId)?.pruned).toBe(false)
    expect(applied.workspace.nodes[0].position).toEqual(latest.nodes[0].position)
    expect(applied.workspace.revision).toBe(latest.revision)
    const undone = reverseTreeUndo(applied.workspace, applied.change.undo, false)
    expect(undone.groups?.[0].activeNodeId).toBe(liveActiveId)
    expect(undone.nodes.every((node) => !node.pruned)).toBe(true)
    expect(undone.nodes[0].position).toEqual(latest.nodes[0].position)
  })

  it('preserves ordered prepared plan IDs and links across presentation drift', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'plan', run: false, steps: [
      { id: 'first', parent: { node_id: tree.nodes[0].id }, prompt: 'First', converters: [] },
      { id: 'second', parent: { step_id: 'first' }, prompt: 'Second', converters: [] },
    ] })
    const review = prepareAssistantProposal(tree, proposed)
    const applied = applyAssistantReview({ ...tree, revision: tree.revision + 1 }, proposed, review)
    if (applied.kind !== 'plan' || review.kind !== 'plan') throw new Error('Expected plan')
    expect(applied.addedNodeIds).toEqual(review.addedNodeIds)
    expect(applied.workspace.nodes.slice(1)).toEqual(review.workspace.nodes.slice(1))
    expect(applied.change.undo).toBeNull()
    expect(applied.change.selectionId).toBe(review.selectionId)
    expect(applied.nodeIds).toEqual([])
  })

  it('rejects semantic drift at application and does not silently re-plan a captured review', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'mutate', commands: [{ type: 'add', parentId: null, prompt: 'New root' }] })
    const review = prepareAssistantProposal(tree, proposed)
    expect(() => applyAssistantReview({ ...tree, systemPrompt: 'Different semantics' }, proposed, review)).toThrow(/re-plan/)
    expect(() => applyAssistantReview({ ...tree, revision: tree.revision + 1 }, proposed)).toThrow(/re-plan/)
  })

  it('keeps execution reviews unchanged and supports strict-revision preparation for legacy callers', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'run', node_ids: [tree.nodes[0].id] })
    const review = prepareAssistantProposal(tree, proposed)
    expect(applyAssistantReview({ ...tree, revision: tree.revision + 1 }, proposed, review)).toBe(review)
    expect(applyAssistantReview(tree, proposed)).toMatchObject({ kind: 'run', nodeIds: [tree.nodes[0].id], operations: 1 })
  })

  it('keeps approvals valid across layout changes only with the original host precondition', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'mutate', commands: [{ type: 'edit', nodeId: tree.nodes[0].id, prompt: 'Changed prompt', converters: [] }] })
    const captured = captureAssistantPrecondition(tree)
    const moved = { ...applyTreeCommand(tree, { type: 'move', nodeId: tree.nodes[0].id, position: { x: 400, y: 250 } }), revision: tree.revision + 1 }
    expect(() => prepareAssistantProposal(moved, proposed)).toThrow(/re-plan/)
    const review = prepareAssistantProposal(moved, proposed, captured)
    expect(review.changes[0].before?.prompt).toBe(tree.nodes[0].prompt)
    expect(review.changes[0].after?.prompt).toBe('Changed prompt')
    expect(review.precondition).toEqual(captured)
    expect(review.affectedNodeIds).toEqual([tree.nodes[0].id])
    expect(review.operations).toBe(0)
  })

  it.each(['system', 'pipeline', 'evidence', 'budget'] as const)('invalidates the host precondition on a %s change even without a revision change', (field) => {
    const tree = observed(fixture())
    const sourcePiece = tree.nodes[0].messages?.[1].message_pieces[0]
    if (field === 'evidence' && sourcePiece) sourcePiece.converted_value = `${'Same response prefix'.repeat(200)}Original suffix`
    const proposed = proposal(tree, { kind: 'mutate', commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Child' }] })
    const captured = captureAssistantPrecondition(tree)
    const changed: TreeWorkspace = JSON.parse(JSON.stringify(tree))
    if (field === 'system') changed.systemPrompt = 'Different system context'
    if (field === 'pipeline') changed.nodes[0].converters = [{ type: 'Base64Converter', params: {} }]
    if (field === 'budget') changed.settings = { ...getTreeSettings(changed), operationBudget: 1 }
    if (field === 'evidence') {
      const piece = changed.nodes[0].messages?.[1].message_pieces[0]
      if (!piece) throw new Error('Missing response')
      piece.converted_value = `${'Same response prefix'.repeat(200)}Changed beyond the abbreviated preview`
      expect(createAssistantContext(changed, null).nodes[0].response_preview).toBe(createAssistantContext(tree, null).nodes[0].response_preview)
    }
    expect(() => prepareAssistantProposal(changed, proposed, captured)).toThrow(/re-plan/)
  })

  it('prepares one deeply immutable candidate and preserves its exact generated IDs for application', () => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'mutate', commands: [
      { type: 'childVariants', nodeId: tree.nodes[0].id, variants: [{ prompt: 'Variant', converters: [{ type: 'Base64Converter', params: {} }] }] },
    ] })
    const review = prepareAssistantProposal(tree, proposed)
    if (review.kind !== 'mutate') throw new Error('Expected mutation')
    expect(Object.isFrozen(review)).toBe(true)
    expect(Object.isFrozen(review.workspace.nodes)).toBe(true)
    expect(Object.isFrozen(review.changes[0].after?.converters)).toBe(true)
    expect(Object.isFrozen(tree.nodes)).toBe(false)
    expect(review.addedNodeIds).toEqual(review.workspace.nodes.slice(1).map((node) => node.id))
    expect(review.nodeIds).toEqual([])
    expect(() => validatePreparedAssistantProposal(tree, proposed, review)).not.toThrow()
    expect(() => validatePreparedAssistantProposal(tree, { ...proposed, action: { kind: 'run', node_ids: [tree.nodes[0].id] } }, review)).toThrow(/reviewed/)
  })

  it('reports every descendant whose visibility changes, not just the named prune root', () => {
    let tree = fixture()
    tree = applyTreeCommand(tree, { type: 'add', parentId: tree.nodes[0].id, prompt: 'Child' })
    tree = applyTreeCommand(tree, { type: 'add', parentId: tree.nodes[1].id, prompt: 'Grandchild' })
    const review = prepareAssistantProposal(tree, proposal(tree, { kind: 'mutate', commands: [{ type: 'prune', nodeId: tree.nodes[0].id, pruned: true }] }))
    expect(review.affectedNodeIds).toEqual(tree.nodes.map((node) => node.id))
    expect(review.changes).toHaveLength(3)
  })

  it('separates created draft IDs from runnable IDs for a draft-only plan', () => {
    const tree = fixture()
    const review = prepareAssistantProposal(tree, proposal(tree, { kind: 'plan', run: false, steps: [
      { id: 'first', parent: { node_id: tree.nodes[0].id }, prompt: 'Draft child', converters: [] },
      { id: 'next', parent: { step_id: 'first' }, prompt: 'Draft grandchild', converters: [] },
    ] }))
    expect(review.addedNodeIds).toHaveLength(2)
    expect(review.nodeIds).toEqual([])
    expect(review.operations).toBe(0)
    expect(review.changes.map((change) => change.after?.prompt)).toEqual(['Draft child', 'Draft grandchild'])
  })

  it('builds an ordered multi-level plan and executes only its new drafts', () => {
    const tree = observed(fixture())
    const prepared = prepareAssistantProposal(tree, proposal(tree, { kind: 'plan', run: true, steps: [
      { id: 'first', parent: { node_id: tree.nodes[0].id }, prompt: 'First probe', converters: [] },
      { id: 'second', parent: { step_id: 'first' }, prompt: 'Second probe', converters: [] },
      { id: 'third', parent: { step_id: 'second' }, prompt: 'Third probe', converters: [] },
    ] }))
    if (prepared.kind !== 'plan') throw new Error('Expected plan')
    expect(prepared.operations).toBe(3)
    expect(prepared.nodeIds).toEqual(prepared.workspace.nodes.slice(1).map((node) => node.id))
    expect(prepared.workspace.nodes[2].parentId).toBe(prepared.workspace.nodes[1].id)
    expect(prepared.workspace.nodes[3].parentId).toBe(prepared.workspace.nodes[2].id)
    expect(tree.nodes).toHaveLength(1)
  })
  it('rejects plan forward references, duplicate step IDs and unknown parents atomically', () => {
    const tree = fixture()
    for (const parent of [{ step_id: 'later' }, { node_id: 'missing' }]) {
      expect(() => prepareAssistantProposal(tree, proposal(tree, { kind: 'plan', run: false, steps: [
        { id: 'first', parent, prompt: 'Probe', converters: [] },
      ] }))).toThrow()
    }
    expect(() => prepareAssistantProposal(tree, proposal(tree, { kind: 'plan', run: false, steps: [
      { id: 'same', parent: null, prompt: 'Probe', converters: [] },
      { id: 'same', parent: { step_id: 'same' }, prompt: 'Probe', converters: [] },
    ] }))).toThrow(/unique/)
    expect(tree.nodes).toHaveLength(1)
  })
  it('checks a runnable plan against the workspace budget before adding any drafts', () => {
    const tree = observed(fixture())
    tree.settings = { ...getTreeSettings(tree), operationBudget: 1 }
    const action: TreeAssistantAction = { kind: 'plan', run: true, steps: [
      { id: 'first', parent: { node_id: tree.nodes[0].id }, prompt: 'Probe', converters: [] },
      { id: 'second', parent: { step_id: 'first' }, prompt: 'Follow up', converters: [] },
    ] }
    expect(() => prepareAssistantProposal(tree, proposal(tree, action))).toThrow(/1-call budget/)
    expect(tree.nodes).toHaveLength(1)
    expect(prepareAssistantProposal(tree, proposal(tree, { ...action, run: false })).operations).toBe(0)
  })
  it('restricts autonomous effects to the granted subtree and remaining budget', () => {
    const tree = observed(fixture())
    const grant = { root_node_id: tree.nodes[0].id, remaining_operations: 1, remaining_turns: 3, goal: 'Evaluate' }
    const allowed = proposal(tree, { kind: 'plan', run: true, steps: [
      { id: 'child', parent: { node_id: tree.nodes[0].id }, prompt: 'Probe', converters: [] },
    ] })
    expect(validateAutonomousProposal(tree, allowed, grant)).toBe(1)
    expect(() => validateAutonomousProposal(tree, allowed, { ...grant, remaining_operations: 0 })).toThrow(/budget/)
    for (const type of ['sample', 'fork', 'keep'] as const) {
      const command = type === 'sample' ? { type, nodeId: grant.root_node_id, count: 1 }
        : type === 'fork' ? { type, nodeId: grant.root_node_id, prompt: 'Fork', converters: [] } : { type, nodeId: grant.root_node_id }
      expect(() => validateAutonomousProposal(tree, proposal(tree, { kind: 'mutate', commands: [command] }), grant)).toThrow(/outside/)
    }
    expect(() => validateAutonomousProposal(tree, proposal(tree, { kind: 'plan', run: false, steps: [
      { id: 'outside', parent: null, prompt: 'Probe', converters: [] },
    ] }), grant)).toThrow(/escapes/)
  })
  it('projects saved context without labels or old attempts and marks truncated responses', () => {
    const tree = observed(fixture())
    const reply = tree.nodes[0].messages?.[1].message_pieces[0]
    if (!reply) throw new Error('Missing response fixture')
    reply.converted_value = 'R'.repeat(2400)
    const context = createAssistantContext(tree, tree.nodes[0].id)
    expect(context).toMatchObject({ workspace_id: tree.id, revision: tree.revision, selected_node_id: tree.nodes[0].id })
    expect(context.nodes[0].response_preview).toHaveLength(2000)
    expect(context.nodes[0].response_truncated).toBe(true)
    expect(context.nodes[0].attempt_id).toBe(tree.nodes[0].attemptId)
    expect(JSON.stringify(context)).not.toContain('private-label')
    expect(context).not.toHaveProperty('systemPrompt')
    expect(context.nodes[0]).not.toHaveProperty('attempts')
  })
  it('rejects an oversized prompt instead of silently sending an incomplete strategy', () => {
    const tree = fixture()
    tree.nodes[0].prompt = 'X'.repeat(32_001)
    expect(() => createAssistantContext(tree, null)).toThrow(/32000/)
  })
  it('supports an explicit draft-only override without mutating the workspace', () => {
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true, confirmRuns: false }
    const proposed = proposal(tree, { kind: 'mutate', run: false, commands: [
      { type: 'childVariants', nodeId: tree.nodes[0].id, variants: [{ prompt: 'Follow up', converters: [] }] },
      { type: 'sample', nodeId: tree.nodes[0].id, count: 2 },
    ] })
    const prepared = prepareAssistantProposal(tree, proposed)
    expect(prepared.kind).toBe('mutate')
    expect(previewAssistantProposal(tree, proposed)).toMatchObject({ operations: 0, description: expect.stringContaining('Explicit draft-only override') })
    if (prepared.kind !== 'mutate') throw new Error('Wrong action')
    expect(prepared.workspace.nodes).toHaveLength(4)
    expect(prepared.workspace.nodes.every((node) => node.status === 'draft')).toBe(true)
    expect(tree.nodes).toHaveLength(1)
    expect(tree.nodes[0].status).toBe('draft')
  })
  it('rejects the whole batch when a later edit is invalid', () => {
    const tree = fixture()
    const incoming = proposal(tree, { kind: 'mutate', commands: [
      { type: 'add', parentId: tree.nodes[0].id, prompt: 'Valid first edit' },
      { type: 'edit', nodeId: 'missing-node', prompt: 'Invalid later edit', converters: [] },
    ] })
    expect(() => prepareAssistantProposal(tree, incoming)).toThrow(/node/i)
    expect(tree.nodes).toHaveLength(1)
  })
  it('does not silently use incomplete context beyond its byte limit', () => {
    const tree = fixture()
    tree.nodes = Array.from({ length: 30 }, (_, index) => ({ ...tree.nodes[0], id: `root-${index}`, attemptId: `attempt-${index}`, prompt: 'X'.repeat(20_000) }))
    expect(() => createAssistantContext(tree, null)).toThrow(/context limit/)
  })
  it('rejects credentials hidden in converter parameters before sending context', () => {
    const tree = fixture()
    tree.nodes[0].converters = [{ type: 'LLMConverter', params: { api_key: 'not-for-chat' } }]
    expect(() => createAssistantContext(tree, null)).toThrow(/credentials/)
  })
  it('rejects stale, cross-workspace and already resolved proposals', () => {
    const tree = fixture()
    const valid = proposal(tree, { kind: 'run', node_ids: [tree.nodes[0].id] })
    expect(() => prepareAssistantProposal({ ...tree, revision: 2 }, valid)).toThrow(/tree changed/)
    expect(() => prepareAssistantProposal({ ...tree, id: 'another' }, valid)).toThrow(/tree changed/)
    expect(() => prepareAssistantProposal(tree, { ...valid, status: 'applied' })).toThrow(/already/)
  })
  it.each([
    { type: 'settings', settings: { operationBudget: 100_000 } },
    { type: 'edit', nodeId: 'node', prompt: 'test', converters: [], status: 'completed' },
    { type: 'score', nodeId: 'node', result: { score_value: 1 } },
    { type: 'retry', nodeId: 'node', scope: 'everything' },
    { type: '__proto__' },
  ])('rejects forbidden or malformed model commands: %j', (command: unknown) => {
    const tree = fixture()
    const incoming: TreeAssistantProposal = JSON.parse(JSON.stringify({
      ...proposal(tree, { kind: 'mutate', commands: [] }), action: { kind: 'mutate', commands: [command] },
    }))
    expect(() => prepareAssistantProposal(tree, incoming)).toThrow()
    expect(tree.nodes).toHaveLength(1)
  })
  it('keeps completed prompts immutable and requires an explicit fork', () => {
    const tree = observed(fixture())
    expect(() => prepareAssistantProposal(tree, proposal(tree, { kind: 'mutate', commands: [
      { type: 'edit', nodeId: tree.nodes[0].id, prompt: 'Rewrite', converters: [] },
    ] }))).toThrow(/immutable/)
    const reset = prepareAssistantProposal(tree, proposal(tree, { kind: 'mutate', commands: [
      { type: 'retry', nodeId: tree.nodes[0].id, scope: 'node' },
    ] }))
    if (reset.kind !== 'mutate') throw new Error('Wrong action')
    expect(reset.workspace.nodes[0].status).toBe('draft')
    expect(reset.workspace.nodes[0].attempts).toHaveLength(1)
  })
  it('runs only explicitly approved nodes in canonical order without adding ancestors', () => {
    const root = fixture()
    const tree = applyTreeCommand(root, { type: 'add', parentId: root.nodes[0].id, prompt: 'Child' })
    const [parent, child] = tree.nodes
    expect(() => prepareAssistantProposal(tree, proposal(tree, { kind: 'run', node_ids: [child.id] }))).toThrow(/parent first/i)
    const prepared = prepareAssistantProposal(tree, proposal(tree, { kind: 'run', node_ids: [child.id, parent.id] }))
    expect(prepared).toMatchObject({ kind: 'run', nodeIds: [parent.id, child.id], operations: 2 })
    expect(() => prepareAssistantProposal(tree, proposal(tree, { kind: 'run', node_ids: [parent.id, parent.id] }))).toThrow(/distinct/)
  })
  it('includes converters and automatic scoring in the exact approval cost', () => {
    const tree = fixture()
    tree.nodes[0].converters = [{ type: 'Base64Converter', params: {} }]
    tree.settings = { ...getTreeSettings(tree), autoScore: true, operationBudget: 2, scorers: [
      { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', highIsRisk: true, scope: 'response' },
    ] }
    const proposed = proposal(tree, { kind: 'run', node_ids: [tree.nodes[0].id] })
    expect(() => prepareAssistantProposal(tree, proposed)).toThrow(/budget/i)
    tree.settings.operationBudget = 3
    expect(previewAssistantProposal(tree, proposed).operations).toBe(3)
  })

  it.each([
    { autoRun: false, intent: undefined, expected: false },
    { autoRun: false, intent: null, expected: false },
    { autoRun: false, intent: false, expected: false },
    { autoRun: false, intent: true, expected: true },
    { autoRun: true, intent: undefined, expected: true },
    { autoRun: true, intent: null, expected: true },
    { autoRun: true, intent: false, expected: false },
    { autoRun: true, intent: true, expected: true },
  ])('freezes additions under policy %j for both action kinds', ({ autoRun, intent, expected }) => {
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun, autoScore: true, scorers: [
      { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', highIsRisk: true, scope: 'response' },
    ] }
    expect(createAssistantContext(tree, null).settings.auto_run).toBe(autoRun)
    const converters = [{ type: 'Base64Converter', params: {} }]
    const actions: TreeAssistantAction[] = [
      { kind: 'mutate', run: intent, commands: [{ type: 'add', parentId: null, prompt: 'New root', converters }] },
      { kind: 'plan', run: intent, steps: [{ id: 'new-root', parent: null, prompt: 'New root', converters }] },
    ]
    for (const action of actions) {
      const proposed = proposal(tree, action)
      const prepared = prepareAssistantProposal(tree, proposed)
      expect(prepared).toMatchObject({ run: expected, operations: expected ? 3 : 0 })
      expect(prepared.nodeIds).toEqual(expected ? prepared.addedNodeIds : [])
      expect(prepared.nodeIds).not.toContain(tree.nodes[0].id)
      expect(Object.isFrozen(prepared.nodeIds)).toBe(true)
      const grant = { root_node_id: null, remaining_operations: 3, remaining_turns: 2, goal: 'Explore' }
      expect(validateAutonomousProposal(tree, proposed, grant, prepared)).toBe(expected ? 3 : 0)
      if (expected) {
        expect(() => validateAutonomousProposal(tree, proposed, { ...grant, remaining_operations: 2 }, prepared)).toThrow(/budget/)
      }
    }
  })

  it.each(['mutate', 'plan'] as const)('fails %s auto-run atomically for an unrun ancestor and allows explicit draft-only', (kind) => {
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true }
    const action: TreeAssistantAction = kind === 'mutate'
      ? { kind, commands: [{ type: 'add', parentId: tree.nodes[0].id, prompt: 'Blocked child' }] }
      : { kind, steps: [{ id: 'child', parent: { node_id: tree.nodes[0].id }, prompt: 'Blocked child', converters: [] }] }
    expect(() => prepareAssistantProposal(tree, proposal(tree, action))).toThrow(/parent first.*run: false/i)
    expect(tree.nodes).toHaveLength(1)
    expect(prepareAssistantProposal(tree, proposal(tree, { ...action, run: false }))).toMatchObject({ run: false, operations: 0, nodeIds: [] })
  })

  it.each(['retry', 'edit', 'prune'] as const)('does not auto-run old nodes after %s without additions', (type) => {
    const tree = type === 'retry' ? observed(fixture()) : fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true }
    const nodeId = tree.nodes[0].id
    const command = type === 'retry' ? { type, nodeId, scope: 'node' as const }
      : type === 'edit' ? { type, nodeId, prompt: 'Edited', converters: [] } : { type, nodeId, pruned: true }
    const prepared = prepareAssistantProposal(tree, proposal(tree, { kind: 'mutate', run: true, commands: [command] }))
    expect(prepared).toMatchObject({ run: false, operations: 0, nodeIds: [], addedNodeIds: [] })
  })

  it('validates mutation auto-run costs including converters and scoring before saving', () => {
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true, autoScore: true, operationBudget: 2, scorers: [
      { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', highIsRisk: true, scope: 'response' },
    ] }
    const proposed = proposal(tree, { kind: 'mutate', commands: [
      { type: 'add', parentId: null, prompt: 'New root', converters: [{ type: 'Base64Converter', params: {} }] },
    ] })
    expect(() => prepareAssistantProposal(tree, proposed)).toThrow(/budget/)
    expect(tree.nodes).toHaveLength(1)
  })

  it('allows whole-workspace Auto mode to create the first root in an empty workspace', () => {
    const tree = { ...fixture(), nodes: [] }
    const proposed = proposal(tree, { kind: 'mutate', run: true, commands: [{ type: 'add', parentId: null, prompt: 'First root' }] })
    expect(validateAutonomousProposal(tree, proposed, {
      root_node_id: null, remaining_operations: 1, remaining_turns: 9, goal: 'Start',
    })).toBe(1)
    expect(tree.nodes).toEqual([])
  })

  it.each(['false', 0, 1, {}, []])('rejects malformed nullable run flags: %j', (run: unknown) => {
    const tree = fixture()
    for (const action of [
      { kind: 'mutate', run, commands: [{ type: 'add', parentId: null, prompt: 'Root' }] },
      { kind: 'plan', run, steps: [{ id: 'root', parent: null, prompt: 'Root', converters: [] }] },
    ]) {
      const proposed: TreeAssistantProposal = JSON.parse(JSON.stringify({ ...proposal(tree, { kind: 'mutate', commands: [] }), action }))
      expect(() => prepareAssistantProposal(tree, proposed)).toThrow(/Invalid/)
    }
  })

  it.each([undefined, false, 0, '', 'missing'])('rejects malformed or missing Auto mode roots: %j', (root: unknown) => {
    const tree = fixture()
    const proposed = proposal(tree, { kind: 'mutate', run: false, commands: [{ type: 'add', parentId: null, prompt: 'Root' }] })
    const grant = JSON.parse(JSON.stringify({ root_node_id: root, remaining_operations: 1, remaining_turns: 1, goal: 'Start' }))
    expect(() => validateAutonomousProposal(tree, proposed, grant)).toThrow()
  })
  it('scores only stored assistant evidence using workspace scorers', () => {
    const tree = fixture()
    const score = proposal(tree, { kind: 'score', node_ids: [tree.nodes[0].id] })
    expect(() => prepareAssistantProposal(tree, score)).toThrow(/Select workspace scorers/)
    tree.settings = { ...getTreeSettings(tree), scorers: [
      { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', highIsRisk: true, scope: 'response' },
    ] }
    expect(() => prepareAssistantProposal(tree, score)).toThrow(/recorded/)
    expect(prepareAssistantProposal(observed(tree), score)).toMatchObject({ kind: 'score', operations: 1 })
  })
})
