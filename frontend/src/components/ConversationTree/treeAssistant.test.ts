import type { TreeAssistantAction, TreeAssistantProposal, TreeWorkspace } from '@/types'

import { createAssistantContext, prepareAssistantProposal, previewAssistantProposal } from './treeAssistant'
import { applyTreeCommand, createTreeWorkspace, getTreeSettings } from './treeModel'

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
  it('previews edits atomically without mutating the workspace or auto-running', () => {
    const tree = fixture()
    tree.settings = { ...getTreeSettings(tree), autoRun: true, confirmRuns: false }
    const proposed = proposal(tree, { kind: 'mutate', commands: [
      { type: 'childVariants', nodeId: tree.nodes[0].id, variants: [{ prompt: 'Follow up', converters: [] }] },
      { type: 'sample', nodeId: tree.nodes[0].id, count: 2 },
    ] })
    const prepared = prepareAssistantProposal(tree, proposed)
    expect(prepared.kind).toBe('mutate')
    expect(previewAssistantProposal(tree, proposed)).toMatchObject({ operations: 0, description: expect.stringContaining('Auto-run is suppressed') })
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
