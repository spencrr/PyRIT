import type { BackendMessage, TreeCommand, TreeNode, TreeScoreRun, TreeWorkspace } from '@/types'

import {
  applyTreeCommand, createTreeWorkspace, DEFAULT_TREE_SETTINGS, exportTreePlan, getCurrentAttemptId, getNewRunNodeIds,
  getRunNodeIds, getTreeLabels, getTreePath, getTreeSettings, importTreePlan, isNodeHidden, MAX_FAN_OUT, MAX_RUN_CALLS,
  MAX_TREE_NODES, parseTreeWorkspace, treeSemanticSignature, TREE_ATTEMPT_LABEL, TREE_NODE_LABEL, TREE_WORKSPACE_LABEL,
  validateTreeForkPath,
} from './treeModel'

const CONFIGURATION = {
  name: 'Tree', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: 'System', labels: { operator: 'tester' },
}
const TIME = '2026-09-12T12:00:00.000Z'

function node(id: string, parentId: string | null = null): TreeNode {
  return { id, parentId, prompt: id, converters: [], status: 'draft', kept: false, pruned: false }
}

function workspace(nodes: TreeNode[]): TreeWorkspace {
  return parseTreeWorkspace(JSON.stringify({ ...createTreeWorkspace(CONFIGURATION), nodes }))
}

function complete(input: TreeNode, start = 1): TreeNode {
  const messages = ['user', 'assistant'].map((role: string, index: number): BackendMessage => ({
    role, turn_number: start + index, created_at: TIME,
    message_pieces: [{
      id: `${input.id}-piece-${index}`, original_value_data_type: 'text', converted_value_data_type: 'text',
      original_value: role === 'user' ? input.prompt : 'reply', converted_value: role === 'user' ? input.prompt : 'reply',
      scores: [], response_error: 'none',
    }],
  }))
  return {
    ...input, status: 'completed', attackResultId: `${input.id}-attack`, conversationId: `${input.id}-conversation`,
    lastSequence: start + 1, messages,
  }
}

function failed(input: TreeNode, start = 1): TreeNode {
  return { ...complete(input, start), status: 'error', error: 'Inspect history' }
}

function imported(parent: TreeNode, id: string, prompt: string, start: number): TreeNode {
  return {
    ...complete({ ...node(id, parent.id), prompt }, start),
    attackResultId: parent.attackResultId,
    conversationId: parent.conversationId,
    parentAttemptId: getCurrentAttemptId(parent),
    attemptId: `${id}:observed`,
    importedFromBackend: true,
  }
}

describe('treeModel', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should create an isolated revision-zero workspace and apply pure commands', () => {
    const original = createTreeWorkspace(CONFIGURATION)
    const added = applyTreeCommand(original, { type: 'add', parentId: null, prompt: 'hello' })
    expect(original.nodes).toEqual([])
    expect(added.revision).toBe(0)
    expect(added.nodes[0]).toMatchObject({ status: 'draft', prompt: 'hello', pruned: false })
    expect(getCurrentAttemptId({ id: 'legacy-node', attemptId: undefined })).toBe('legacy-node:initial')
    expect(getCurrentAttemptId(added.nodes[0])).toBe(added.nodes[0].attemptId)
    const edited = applyTreeCommand(added, { type: 'edit', nodeId: added.nodes[0].id, prompt: 'updated', converters: [] })
    expect(added.nodes[0].prompt).toBe('hello')
    expect(edited.nodes[0].prompt).toBe('updated')
  })

  it.each<[string, unknown]>([
    ['attemptId', null], ['attemptId', 42], ['attackResultId', null], ['attackResultId', 42],
    ['conversationId', null], ['conversationId', {}], ['lastSequence', null], ['lastSequence', '1'],
    ['error', null], ['error', false], ['parentAttemptId', null], ['parentAttemptId', 42],
    ['messages', null], ['messages', {}], ['scoreRuns', null], ['scoreRuns', {}],
  ])('should reject malformed optional current and archived attempt field %s=%p', (field: string, value: unknown) => {
    const draft = workspace([node('root')])
    expect(() => parseTreeWorkspace(JSON.stringify({
      ...draft, nodes: [{ ...draft.nodes[0], [field]: value }],
    }))).toThrow('Invalid conversation tree')
    const retried = applyTreeCommand(workspace([failed(node('root'))]), { type: 'retry', nodeId: 'root', scope: 'node' })
    expect(() => parseTreeWorkspace(JSON.stringify({
      ...retried,
      nodes: [{ ...retried.nodes[0], attempts: [{ ...retried.nodes[0].attempts?.[0], [field]: value }] }],
    }))).toThrow('Invalid conversation tree')
  })

  it('should preserve legacy omissions without interpreting explicit invalid values as missing', () => {
    const legacy = { ...createTreeWorkspace(CONFIGURATION), nodes: [node('root'), node('child', 'root')] }
    const loaded = parseTreeWorkspace(JSON.stringify(legacy))
    expect(loaded.nodes[0].attemptId).toBe('root:initial')
    expect(loaded.nodes[1].parentAttemptId).toBe('root:initial')
    expect(loaded.nodes[0].attempts).toBeUndefined()
    expect(loaded.nodes[0].scoreRuns).toBeUndefined()
    expect(loaded.schemaVersion).toBe(1)
    expect(legacy.nodes[0].attemptId).toBeUndefined()
  })

  it('should ignore only explicit presentation state in semantic signatures', () => {
    const before = applyTreeCommand(workspace([node('root')]), { type: 'sample', nodeId: 'root', count: 1 })
    const after: TreeWorkspace = {
      ...before, revision: before.revision + 3, createdAt: TIME, updatedAt: TIME,
      nodes: before.nodes.map((item: TreeNode) => ({ ...item, position: { x: 500, y: 700 }, size: { width: 500, height: 500 } })),
      groups: before.groups?.map((group: NonNullable<TreeWorkspace['groups']>[number]) =>
        ({ ...group, collapsed: !group.collapsed, activeNodeId: group.nodeIds[1] })),
      settings: { ...getTreeSettings(before), markdown: true, nodeSize: 'expanded', edgeStyle: 'straight', stackSamples: false, stackVariants: true },
    }
    expect(treeSemanticSignature(after)).toBe(treeSemanticSignature(before))
    expect(treeSemanticSignature({ ...before, settings: undefined })).toBe(treeSemanticSignature(before))
  })

  it('should invalidate semantic identity for configuration, topology, prompts, pipelines, and evidence', () => {
    const before = workspace([complete(node('root')), node('child', 'root')])
    const changes: TreeWorkspace[] = [
      { ...before, name: 'Renamed' },
      { ...before, targetRegistryName: 'other' },
      { ...before, targetIdentifierHash: 'other' },
      { ...before, systemPrompt: 'Other instructions' },
      { ...before, labels: { ...before.labels, operator: 'other' } },
      { ...before, nodes: [before.nodes[0], { ...before.nodes[1], parentId: null, parentAttemptId: undefined }] },
      { ...before, nodes: [before.nodes[0], { ...before.nodes[1], prompt: 'Other input' }] },
      { ...before, nodes: [before.nodes[0], { ...before.nodes[1], converters: [{ type: 'Converter', params: { language: 'fr' } }] }] },
      { ...before, nodes: [before.nodes[0], { ...before.nodes[1], pruned: true }] },
      { ...before, nodes: [{ ...before.nodes[0], messages: before.nodes[0].messages?.map((message: BackendMessage) =>
        ({ ...message, created_at: '2026-09-13T12:00:00.000Z' })) }, before.nodes[1]] },
      { ...before, settings: { ...getTreeSettings(before), objective: 'Different objective' } },
      { ...before, settings: { ...getTreeSettings(before), concurrency: 4 } },
      { ...before, settings: { ...getTreeSettings(before), operationBudget: 10 } },
      { ...before, settings: { ...getTreeSettings(before), autoRun: true } },
    ]
    for (const changed of changes) expect(treeSemanticSignature(changed)).not.toBe(treeSemanticSignature(before))
  })

  it('should canonicalize object key order without discarding converter parameters or score outcomes', () => {
    const before = workspace([{ ...node('root'), converters: [{ type: 'Converter', params: { first: 1, nested: { a: 2, b: 3 } } }] }])
    const reordered = { ...before, nodes: [{ ...before.nodes[0], converters: [{ type: 'Converter', params: { nested: { b: 3, a: 2 }, first: 1 } }] }] }
    expect(treeSemanticSignature(reordered)).toBe(treeSemanticSignature(before))
    const observed = workspace([complete(node('root'))])
    const scored = applyTreeCommand(observed, {
      type: 'score', nodeId: 'root', attemptId: getCurrentAttemptId(observed.nodes[0]),
      result: { id: 'score-run', scorerId: 'scorer', scorerHash: 'hash', status: 'not_applicable', scores: [] },
    })
    expect(treeSemanticSignature(scored)).not.toBe(treeSemanticSignature(observed))
  })

  it('should stamp stable reserved labels on new workspaces and normalize per-node request labels', () => {
    const configuredLabels = {
      operator: 'tester',
      [TREE_WORKSPACE_LABEL]: 'stale-workspace',
      [TREE_NODE_LABEL]: 'stale-node',
    }
    const original = createTreeWorkspace({ ...CONFIGURATION, labels: configuredLabels })
    expect(configuredLabels).toEqual({
      operator: 'tester',
      [TREE_WORKSPACE_LABEL]: 'stale-workspace',
      [TREE_NODE_LABEL]: 'stale-node',
    })
    expect(original.labels).toEqual({ operator: 'tester', [TREE_WORKSPACE_LABEL]: original.id })
    expect(getTreeLabels({
      ...original,
      labels: { ...original.labels, [TREE_WORKSPACE_LABEL]: 'wrong', [TREE_NODE_LABEL]: 'wrong-node' },
    })).toEqual({ operator: 'tester', [TREE_WORKSPACE_LABEL]: original.id })
    expect(getTreeLabels(original, 'child')).toEqual({
      operator: 'tester',
      [TREE_WORKSPACE_LABEL]: original.id,
      [TREE_NODE_LABEL]: 'child',
    })
    const withNode = applyTreeCommand(original, { type: 'add', parentId: null, prompt: 'root' })
    expect(getTreeLabels(withNode, withNode.nodes[0].id)).toMatchObject({
      [TREE_WORKSPACE_LABEL]: withNode.id,
      [TREE_NODE_LABEL]: withNode.nodes[0].id,
      [TREE_ATTEMPT_LABEL]: withNode.nodes[0].attemptId,
    })
    const imported = importTreePlan(JSON.stringify({
      schemaVersion: 1,
      name: 'Plan',
      steps: [{ id: 'root', parentId: null, prompt: 'root', converters: [] }],
    }), CONFIGURATION)
    expect(imported.id).not.toBe(original.id)
    expect(imported.labels[TREE_WORKSPACE_LABEL]).toBe(imported.id)
  })

  it('should prune effectively through ancestors and preserve explicit descendant prunes on restore', () => {
    let tree = workspace([node('root'), node('child', 'root'), node('leaf', 'child')])
    tree = applyTreeCommand(tree, { type: 'prune', nodeId: 'leaf', pruned: true })
    tree = applyTreeCommand(tree, { type: 'prune', nodeId: 'root', pruned: true })
    expect(isNodeHidden(tree, 'child')).toBe(true)
    tree = applyTreeCommand(tree, { type: 'prune', nodeId: 'root', pruned: false })
    expect(isNodeHidden(tree, 'child')).toBe(false)
    expect(isNodeHidden(tree, 'leaf')).toBe(true)
  })

  it('should keep human-selected branches without declaring objective success', () => {
    const original = workspace([node('root'), node('one', 'root'), node('two', 'root'), node('leaf', 'two')])
    const kept = applyTreeCommand(original, { type: 'keep', nodeId: 'one' })
    expect(kept.nodes.find((item: TreeNode) => item.id === 'one')).toMatchObject({ kept: true, status: 'draft' })
    expect(isNodeHidden(kept, 'leaf')).toBe(true)
    expect(original.nodes.every((item: TreeNode) => !item.pruned)).toBe(true)
    expect(() => applyTreeCommand(kept, { type: 'keep', nodeId: 'leaf' })).toThrow('restore')
  })

  it('should stage independent sibling variants and move nodes without editing evidence', () => {
    const original = workspace([complete(node('root')), node('child', 'root')])
    const variants = [{ prompt: 'a', converters: [] }, { prompt: 'b', converters: [] }]
    const staged = applyTreeCommand(original, { type: 'fanOut', nodeId: 'child', variants })
    expect(staged.nodes.slice(2).map((item: TreeNode) => item.parentId)).toEqual(['root', 'root'])
    expect(staged.groups?.[0]).toMatchObject({ kind: 'variant', collapsed: false, nodeIds: [staged.nodes[2].id, staged.nodes[3].id] })
    variants[0].prompt = 'mutated'
    expect(staged.nodes[2].prompt).toBe('a')
    const moved = applyTreeCommand(staged, { type: 'move', nodeId: 'root', position: { x: 50, y: -30 } })
    expect(moved.nodes[0].messages).toEqual(original.nodes[0].messages)
    expect(moved.nodes[0].position).toEqual({ x: 50, y: -30 })
    expect(() => applyTreeCommand(original, { type: 'edit', nodeId: 'root', prompt: 'bad', converters: [] })).toThrow('immutable')
  })

  it('should stage child variants below visible draft or completed nodes while fan-out stays sibling based', () => {
    const root = node('root')
    const completedNode = complete(node('completed'))
    const original = workspace([root, completedNode, failed(node('failed')), { ...node('hidden'), pruned: true }, { ...node('running'), status: 'running' }])
    const variants = [{ prompt: 'left', converters: [] }, { prompt: 'right', converters: [{ type: 'Base64Converter', params: {} }] }]
    const drafted = applyTreeCommand(original, { type: 'childVariants', nodeId: 'root', variants })
    expect(drafted.nodes.slice(original.nodes.length).map((item: TreeNode) => item.parentId)).toEqual(['root', 'root'])
    expect(drafted.groups?.[0]).toMatchObject({ kind: 'variant', collapsed: false })
    const observed = applyTreeCommand(original, { type: 'childVariants', nodeId: 'completed', variants: [{ prompt: 'follow-up', converters: [] }] })
    expect(observed.nodes[original.nodes.length]).toMatchObject({ parentId: 'completed', prompt: 'follow-up', status: 'draft' })
    expect(() => applyTreeCommand(original, { type: 'childVariants', nodeId: 'root', variants: [] })).toThrow('child variants')
    expect(() => applyTreeCommand(original, { type: 'childVariants', nodeId: 'failed', variants })).toThrow('draft or completed')
    expect(() => applyTreeCommand(original, { type: 'childVariants', nodeId: 'running', variants })).toThrow('draft or completed')
    expect(() => applyTreeCommand(original, { type: 'childVariants', nodeId: 'hidden', variants })).toThrow('restore')
  })

  it('should fork only the active subtree with new IDs and no execution evidence', () => {
    const root = complete(node('root'))
    const child = complete(node('child', 'root'), 3)
    const original = workspace([root, child, node('leaf', 'child'), { ...node('hidden', 'child'), pruned: true }])
    const forked = applyTreeCommand(original, {
      type: 'fork', nodeId: 'child', prompt: 'variant', converters: [{ type: 'Base64Converter', params: {} }],
    })
    expect(forked.nodes.slice(0, 4)).toEqual(original.nodes)
    expect(forked.nodes).toHaveLength(6)
    const variant = forked.nodes[4]
    const leaf = forked.nodes[5]
    expect(variant).toMatchObject({ forkedFrom: 'child', parentId: 'root', prompt: 'variant', status: 'draft' })
    expect(variant.messages).toBeUndefined()
    expect(variant.attackResultId).toBeUndefined()
    expect(variant.lastSequence).toBeUndefined()
    expect(leaf).toMatchObject({ forkedFrom: 'leaf', parentId: variant.id, prompt: 'leaf', status: 'draft' })
    expect(getRunNodeIds(forked, variant.id)).toEqual([variant.id, leaf.id])
  })

  it('should return an inclusive ordered path independently of storage order and reject unrelated endpoints', () => {
    const tree = workspace([node('end', 'middle'), node('other'), node('middle', 'start'), node('start')])
    expect(getTreePath(tree, 'start', 'end').map((item: TreeNode) => item.id)).toEqual(['start', 'middle', 'end'])
    expect(getTreePath(tree, 'middle', 'middle')).toEqual([tree.nodes[2]])
    expect(() => getTreePath(tree, 'missing', 'end')).toThrow('node not found')
    expect(() => getTreePath(tree, 'start', 'missing')).toThrow('node not found')
    expect(() => getTreePath(tree, 'start', 'other')).toThrow('descendant')
    expect(() => getTreePath(tree, 'end', 'start')).toThrow('descendant')
    const cyclic = { ...tree, nodes: [node('start'), node('cycle-a', 'cycle-b'), node('cycle-b', 'cycle-a')] }
    expect(() => getTreePath(cyclic, 'start', 'cycle-a')).toThrow('cycle')
  })

  it('should fork exactly the selected path below a branched prefix without copying evidence or sibling branches', () => {
    const score: TreeScoreRun = {
      id: 'score-run', scorerId: 'scorer', scorerHash: 'hash', status: 'complete',
      scores: [{
        id: 'score', message_piece_id: 'start-piece-1', scorer_type: 'Scorer', score_type: 'float_scale',
        score_value: '0.7', timestamp: TIME,
      }],
    }
    const before = applyTreeCommand(workspace([
      complete(node('prefix')),
      complete({ ...node('start', 'prefix'), kept: true }, 3),
      complete({ ...node('middle', 'start'), converters: [{ type: 'OriginalConverter', params: { nested: { mode: 'saved' } } }] }, 5),
      failed(node('end', 'middle'), 7),
      node('prefix-sibling', 'prefix'),
      node('side', 'start'),
      node('side-leaf', 'side'),
      node('beyond-end', 'end'),
      { ...node('pruned-side', 'middle'), pruned: true },
    ]), { type: 'score', nodeId: 'start', attemptId: 'start:initial', result: score })
    const original = JSON.stringify(before)
    const command: Extract<TreeCommand, { type: 'forkPath' }> = {
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Edited first prompt',
      converters: [{ type: 'NewConverter', params: { mode: 'new' } }],
    }
    const forked = applyTreeCommand(before, command)
    const clones = forked.nodes.slice(before.nodes.length)
    expect(clones.map((item: TreeNode) => item.forkedFrom)).toEqual(['start', 'middle', 'end'])
    expect(clones.map((item: TreeNode) => item.parentId)).toEqual(['prefix', clones[0].id, clones[1].id])
    expect(clones.map((item: TreeNode) => item.parentAttemptId)).toEqual([
      getCurrentAttemptId(before.nodes[0]), getCurrentAttemptId(clones[0]), getCurrentAttemptId(clones[1]),
    ])
    expect(clones.map((item: TreeNode) => item.prompt)).toEqual(['Edited first prompt', 'middle', 'end'])
    expect(clones[0].converters).toEqual(command.converters)
    expect(clones[1].converters).toEqual(before.nodes[2].converters)
    const originalIds = new Set(before.nodes.flatMap((item: TreeNode) => [item.id, getCurrentAttemptId(item)]))
    const freshIds = clones.flatMap((item: TreeNode) => [item.id, getCurrentAttemptId(item)])
    expect(new Set(freshIds).size).toBe(clones.length * 2)
    expect(freshIds.every((id: string) => !originalIds.has(id))).toBe(true)
    for (const clone of clones) {
      expect(clone).toMatchObject({ status: 'draft', pruned: false, kept: false })
      for (const field of [
        'attackResultId', 'conversationId', 'lastSequence', 'messages', 'error', 'attempts', 'scoreRuns', 'importedFromBackend',
      ]) expect(clone).not.toHaveProperty(field)
    }
    expect(JSON.stringify(before)).toBe(original)
    expect(forked.nodes.slice(0, before.nodes.length)).toEqual(before.nodes)
    expect(forked.nodes[1].scoreRuns).toEqual([score])
    expect(getNewRunNodeIds(forked, clones.map((item: TreeNode) => item.id))).toEqual(clones.map((item: TreeNode) => item.id))
    command.converters[0].params.mode = 'changed later'
    clones[1].converters[0].params.nested = { mode: 'changed clone' }
    expect(forked.nodes[before.nodes.length].converters[0].params.mode).toBe('new')
    expect(JSON.stringify(before)).toBe(original)
  })

  it('should preserve the starting node parent attempt even when that prefix was retried', () => {
    const original = workspace([complete(node('prefix')), node('start', 'prefix'), node('end', 'start')])
    const retried = applyTreeCommand(original, { type: 'retry', nodeId: 'prefix', scope: 'subtree' })
    retried.nodes[1].parentAttemptId = getCurrentAttemptId(original.nodes[0])
    const forked = applyTreeCommand(retried, {
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Variant', converters: [],
    })
    expect(forked.nodes[3].parentAttemptId).toBe(getCurrentAttemptId(original.nodes[0]))
    expect(forked.nodes[3].parentAttemptId).not.toBe(getCurrentAttemptId(retried.nodes[0]))
    expect(forked.nodes[4].parentAttemptId).toBe(getCurrentAttemptId(forked.nodes[3]))
    expect(forked.nodes.slice(0, 3)).toEqual(retried.nodes)
  })

  it('should support a single-node root fork and discard archived attempts', () => {
    const retried = applyTreeCommand(workspace([failed(node('root')), node('child', 'root')]), {
      type: 'retry', nodeId: 'root', scope: 'subtree',
    })
    const forked = applyTreeCommand(retried, {
      type: 'forkPath', nodeId: 'root', descendantId: 'root', prompt: 'One prompt', converters: [],
    })
    expect(forked.nodes).toHaveLength(3)
    expect(forked.nodes[2]).toMatchObject({ parentId: null, forkedFrom: 'root', prompt: 'One prompt', status: 'draft' })
    expect(forked.nodes[2].parentAttemptId).toBeUndefined()
    expect(forked.nodes[2].attempts).toBeUndefined()
    expect(forked.nodes[0].attempts).toHaveLength(1)
  })

  it.each([
    ['missing', 'end', 'node not found'],
    ['start', 'missing', 'node not found'],
    ['start', 'other', 'descendant'],
    ['end', 'start', 'descendant'],
  ])('should reject invalid path endpoints %s to %s', (startId: string, endId: string, error: string) => {
    const tree = workspace([node('start'), node('end', 'start'), node('other')])
    const original = JSON.stringify(tree)
    expect(() => applyTreeCommand(tree, {
      type: 'forkPath', nodeId: startId, descendantId: endId, prompt: 'Changed', converters: [],
    })).toThrow(error)
    expect(JSON.stringify(tree)).toBe(original)
  })

  it.each(['prefix', 'start', 'middle', 'end'])('should reject paths hidden by pruning %s', (prunedId: string) => {
    const tree = workspace([
      node('prefix'), node('start', 'prefix'), node('middle', 'start'), node('end', 'middle'),
    ].map((item: TreeNode) => ({ ...item, pruned: item.id === prunedId })))
    const command: Extract<TreeCommand, { type: 'forkPath' }> = {
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Changed', converters: [],
    }
    expect(() => validateTreeForkPath(tree, command)).toThrow('restore hidden branches')
    expect(() => applyTreeCommand(tree, command)).toThrow('restore hidden branches')
  })

  it.each(['start', 'middle', 'end'])('should reject running source %s without blocking unrelated running siblings', (runningId: string) => {
    const originals = [complete(node('start')), complete(node('middle', 'start'), 3), node('end', 'middle')]
    const runningIndex = originals.findIndex((item: TreeNode) => item.id === runningId)
    const tree = workspace(originals.map((item: TreeNode, index: number): TreeNode =>
      index < runningIndex ? item : { ...node(item.id, item.parentId), status: index === runningIndex ? 'running' : 'draft' }))
    const command: Extract<TreeCommand, { type: 'forkPath' }> = {
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Changed', converters: [],
    }
    expect(() => validateTreeForkPath(tree, command)).toThrow('running source')
    expect(() => applyTreeCommand(tree, command)).toThrow('running source')
    const unrelated = workspace([...originals, { ...node('running-side', 'start'), status: 'running' }])
    expect(applyTreeCommand(unrelated, command).nodes.slice(4).map((item: TreeNode) => item.forkedFrom))
      .toEqual(['start', 'middle', 'end'])
    expect(() => applyTreeCommand(unrelated, { type: 'fork', nodeId: 'start', prompt: 'Changed', converters: [] })).toThrow('running descendants')
  })

  it('should enforce the node limit and validate edited prompts and pipelines before creating a path', () => {
    const tree = workspace([
      node('start'), node('end', 'start'),
      ...Array.from({ length: MAX_TREE_NODES - 4 }, (_: unknown, index: number) => node(`other-${index}`)),
    ])
    const command: Extract<TreeCommand, { type: 'forkPath' }> = {
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Changed', converters: [],
    }
    const random = jest.spyOn(crypto, 'getRandomValues')
    expect(validateTreeForkPath(tree, command).map((item: TreeNode) => item.id)).toEqual(['start', 'end'])
    expect(random).not.toHaveBeenCalled()
    random.mockRestore()
    expect(applyTreeCommand(tree, command).nodes).toHaveLength(MAX_TREE_NODES)
    const crowded = workspace([...tree.nodes, node('overflow')])
    expect(() => validateTreeForkPath(crowded, command)).toThrow(`maximum ${MAX_TREE_NODES}`)
    expect(() => applyTreeCommand(crowded, command)).toThrow(`maximum ${MAX_TREE_NODES}`)
    for (const invalid of [
      { ...command, prompt: '  ' },
      { ...command, converters: [{ type: 'Converter', params: { api_key: 'not-a-real-key' } }] },
      { ...command, converters: Array.from({ length: MAX_RUN_CALLS + 1 }, () => ({ type: 'Converter', params: {} })) },
    ]) {
      expect(() => validateTreeForkPath(tree, invalid)).toThrow('Invalid conversation tree')
      expect(() => applyTreeCommand(tree, invalid)).toThrow('Invalid conversation tree')
    }
  })

  it('should sample saved nodes as sibling drafts without carrying evidence or state', () => {
    const original = workspace([
      node('root'),
      { ...node('saved', 'root'), converters: [{ type: 'Base64Converter', params: { prefix: 'saved' } }] },
      failed(node('failed')),
      { ...node('hidden', 'root'), pruned: true },
      { ...node('running'), status: 'running', attackResultId: 'running-attack', conversationId: 'running-conversation' },
    ])
    const sampled = applyTreeCommand(original, { type: 'sample', nodeId: 'saved', count: 2 })
    const clones = sampled.nodes.slice(original.nodes.length)
    expect(clones).toHaveLength(2)
    expect(sampled.groups?.[0]).toMatchObject({
      kind: 'sample',
      collapsed: true,
      activeNodeId: 'saved',
      nodeIds: ['saved', ...clones.map((clone: TreeNode) => clone.id)],
    })
    for (const clone of clones) {
      expect(clone).toMatchObject({
        parentId: 'root',
        prompt: 'saved',
        converters: [{ type: 'Base64Converter', params: { prefix: 'saved' } }],
        status: 'draft',
        forkedFrom: 'saved',
      })
      expect(clone.attackResultId).toBeUndefined()
      expect(clone.messages).toBeUndefined()
      expect(clone.error).toBeUndefined()
    }
    const retriedFailure = applyTreeCommand(original, { type: 'sample', nodeId: 'failed', count: 1 })
    expect(retriedFailure.nodes[original.nodes.length]).toMatchObject({ parentId: null, forkedFrom: 'failed', status: 'draft' })
    const completedSamples = applyTreeCommand(workspace([complete(node('completed'))]), { type: 'sample', nodeId: 'completed', count: 1 })
    expect(completedSamples.nodes[1]).toMatchObject({ parentId: null, forkedFrom: 'completed', status: 'draft' })
    expect(() => applyTreeCommand(original, { type: 'sample', nodeId: 'saved', count: 0 })).toThrow('samples')
    expect(() => applyTreeCommand(original, { type: 'sample', nodeId: 'saved', count: MAX_FAN_OUT + 1 })).toThrow('samples')
    expect(() => applyTreeCommand(original, { type: 'sample', nodeId: 'running', count: 1 })).toThrow('Recover')
    expect(() => applyTreeCommand(original, { type: 'sample', nodeId: 'hidden', count: 1 })).toThrow('restore')
  })

  it('should retry in place, archive observed attempts, and clear descendant current history', () => {
    const root = complete(node('root'))
    const child = {
      ...complete({ ...node('child', 'root'), converters: [{ type: 'Base64Converter', params: { prefix: 'child' } }] }, 3),
      position: { x: 2, y: 4 },
    }
    const leaf = complete({ ...node('leaf', 'child'), converters: [{ type: 'ReverseConverter', params: {} }] }, 5)
    const hidden = { ...failed(node('hidden', 'child'), 7), pruned: true }
    const original = workspace([
      root,
      child,
      leaf,
      node('draft', 'root'),
      hidden,
      { ...node('running', 'root'), status: 'running', attackResultId: 'retry-attack', conversationId: 'retry-conversation' },
    ])
    const retried = applyTreeCommand(original, { type: 'retry', nodeId: 'child', scope: 'subtree' })
    expect(retried.nodes).toHaveLength(original.nodes.length)
    const retryChild = retried.nodes.find((item: TreeNode) => item.id === 'child')
    const retryLeaf = retried.nodes.find((item: TreeNode) => item.id === 'leaf')
    const retryHidden = retried.nodes.find((item: TreeNode) => item.id === 'hidden')
    expect(retryChild).toMatchObject({
      parentId: 'root',
      prompt: 'child',
      converters: [{ type: 'Base64Converter', params: { prefix: 'child' } }],
      status: 'draft',
    })
    expect(retryChild?.position).toEqual({ x: 2, y: 4 })
    expect(retryChild.attackResultId).toBeUndefined()
    expect(retryChild.messages).toBeUndefined()
    expect(retryChild.attempts?.[0]).toMatchObject({
      attemptId: original.nodes[1].attemptId,
      status: 'completed',
      messages: original.nodes[1].messages,
    })
    expect(retryChild.attemptId).not.toBe(original.nodes[1].attemptId)
    expect(retryLeaf).toMatchObject({
      parentId: 'child',
      prompt: 'leaf',
      converters: [{ type: 'ReverseConverter', params: {} }],
      status: 'draft',
    })
    expect(retryLeaf?.attempts?.[0].attemptId).toBe(original.nodes[2].attemptId)
    expect(retryLeaf?.attemptId).not.toBe(original.nodes[2].attemptId)
    expect(retryLeaf?.parentAttemptId).toBe(retryChild?.attemptId)
    expect(retryHidden?.attempts?.[0].attemptId).toBe(original.nodes[4].attemptId)
    expect(retryHidden?.status).toBe('draft')
    expect(getRunNodeIds(retried, 'child')).toEqual(['child', 'leaf'])
    const singleRetry = applyTreeCommand(original, { type: 'retry', nodeId: 'child', scope: 'node' })
    expect(singleRetry.nodes).toHaveLength(original.nodes.length)
    expect(singleRetry.nodes.find((item: TreeNode) => item.id === 'child')).toMatchObject({ parentId: 'root', status: 'draft' })
    expect(singleRetry.nodes.find((item: TreeNode) => item.id === 'leaf')).toMatchObject({ status: 'draft' })
    const retryableRoot = applyTreeCommand(workspace([complete(node('root')), complete(node('leaf', 'root'), 3)]), {
      type: 'retry',
      nodeId: 'root',
      scope: 'node',
    })
    expect(retryableRoot.nodes.find((item: TreeNode) => item.id === 'root')).toMatchObject({ parentId: null, status: 'draft' })
    const before = JSON.stringify(original)
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'draft', scope: 'node' })).toThrow('completed or failed')
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'running', scope: 'node' })).toThrow('Recover')
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'hidden', scope: 'node' })).toThrow('restore')
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'root', scope: 'node' })).toThrow('running descendants')
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'running', scope: 'subtree' })).toThrow('Recover')
    expect(() => applyTreeCommand(original, { type: 'retry', nodeId: 'hidden', scope: 'subtree' })).toThrow('restore')
    expect(JSON.stringify(original)).toBe(before)
  })

  it('should schedule breadth-first by default and allow deterministic depth-first traversal', () => {
    const tree = workspace([node('leaf', 'child'), node('child', 'root'), node('root'), node('other')])
    expect(getTreeSettings(parseTreeWorkspace(JSON.stringify({ ...tree, settings: undefined })))).toEqual(DEFAULT_TREE_SETTINGS)
    expect(getRunNodeIds(tree)).toEqual(['root', 'other', 'child', 'leaf'])
    const depthFirst = applyTreeCommand(tree, {
      type: 'settings',
      settings: { ...getTreeSettings(tree), traversal: 'depth-first' },
    })
    expect(getRunNodeIds(depthFirst)).toEqual(['root', 'child', 'leaf', 'other'])
    expect(getRunNodeIds(tree, 'child')).toEqual([])
    tree.nodes[2] = complete(node('root'))
    expect(getRunNodeIds(tree, 'child')).toEqual(['child', 'leaf'])
  })

  it('should block descendants of failed or interrupted parents without false cycle errors', () => {
    const tree = workspace([
      { ...node('failed'), status: 'error', error: 'Inspect history' },
      node('a', 'failed'), node('b', 'a'),
      { ...node('running'), status: 'running' }, node('c', 'running'), node('independent'),
    ])
    expect(getRunNodeIds(tree)).toEqual(['independent'])
    expect(parseTreeWorkspace(JSON.stringify(tree)).nodes[3].status).toBe('running')
  })

  it('should enforce sends-plus-converters budget before execution', () => {
    const nodes = Array.from({ length: MAX_RUN_CALLS }, (_: unknown, index: number) => node(`n${index}`, index ? `n${index - 1}` : null))
    const tree = workspace(nodes)
    expect(getRunNodeIds(tree)).toHaveLength(MAX_RUN_CALLS)
    tree.nodes[0].converters = [{ type: 'Base64Converter', params: {} }]
    expect(() => getRunNodeIds(tree)).toThrow('budget')
  })

  it('should compute auto-run queues only from requested new drafts and reject invalid selections', () => {
    const tree = workspace([complete(node('root')), node('staged-parent', 'root'), node('new-child', 'staged-parent'), node('unrelated')])
    expect(getNewRunNodeIds(tree, [])).toEqual([])
    expect(getNewRunNodeIds(tree, ['new-child', 'staged-parent'])).toEqual(['staged-parent', 'new-child'])
    expect(getNewRunNodeIds(tree, ['unrelated'])).toEqual(['unrelated'])
    expect(() => getNewRunNodeIds(tree, ['new-child'])).toThrow('Run the parent first')
    expect(() => getNewRunNodeIds(tree, ['staged-parent', 'staged-parent'])).toThrow('duplicate')
    expect(() => getNewRunNodeIds(tree, ['missing'])).toThrow('node not found')
    expect(() => getNewRunNodeIds(workspace([{ ...node('hidden'), pruned: true }]), ['hidden'])).toThrow('Pruned')
    expect(() => getNewRunNodeIds(workspace([complete(node('observed'))]), ['observed'])).toThrow('pristine drafts')
    const expensive = workspace(Array.from({ length: MAX_RUN_CALLS }, (_: unknown, index: number) => node(`draft-${index}`)))
    expensive.nodes[0].converters = [{ type: 'Base64Converter', params: {} }]
    expect(() => getNewRunNodeIds(expensive, expensive.nodes.map((item: TreeNode) => item.id))).toThrow('budget')
  })

  it('should validate settings budgets and append score overlays only to the current attempt', () => {
    const observed = workspace([complete(node('root'))])
    expect(() => applyTreeCommand(observed, {
      type: 'settings',
      settings: { ...getTreeSettings(observed), operationBudget: 0 },
    })).toThrow('operation budget')
    const pieceId = observed.nodes[0].messages?.[1].message_pieces[0].id ?? 'missing-piece'
    const first: TreeScoreRun = {
      id: 'run-1',
      scorerId: 'judge',
      scorerHash: 'judge-hash',
      status: 'complete',
      scores: [{ id: 'score-1', message_piece_id: pieceId, scorer_type: 'Judge', score_type: 'float_scale', score_value: '0.8', timestamp: TIME }],
    }
    const second: TreeScoreRun = {
      id: 'run-2',
      scorerId: 'judge',
      scorerHash: 'judge-hash',
      status: 'error',
      scores: [],
      error: 'Scorer failed',
    }
    const scored = applyTreeCommand(observed, { type: 'score', nodeId: 'root', attemptId: observed.nodes[0].attemptId!, result: first })
    const rescored = applyTreeCommand(scored, { type: 'score', nodeId: 'root', attemptId: observed.nodes[0].attemptId!, result: second })
    expect(rescored.nodes[0].scoreRuns).toEqual([first, second])
    const retried = applyTreeCommand(rescored, { type: 'retry', nodeId: 'root', scope: 'node' })
    expect(retried.nodes[0].scoreRuns).toBeUndefined()
    expect(retried.nodes[0].attempts?.[0].scoreRuns).toEqual([first, second])
    expect(() => applyTreeCommand(retried, {
      type: 'score',
      nodeId: 'root',
      attemptId: retried.nodes[0].attempts?.[0].attemptId ?? '',
      result: first,
    })).toThrow('current attempt')
  })

  it('should keep collapsed groups purely presentational for queueing', () => {
    const sampled = applyTreeCommand(workspace([complete(node('root'))]), { type: 'sample', nodeId: 'root', count: 2 })
    expect(sampled.groups?.[0]).toMatchObject({ kind: 'sample', collapsed: true })
    const created = (sampled.groups?.[0].nodeIds ?? []).filter((id: string) => id !== 'root')
    expect(sampled.groups?.[0].nodeIds[0]).toBe('root')
    expect(sampled.groups?.[0].activeNodeId).toBe('root')
    expect(created).toHaveLength(2)
    expect(getNewRunNodeIds(sampled, created)).toEqual(created)
    expect(created.every((id: string) => !isNodeHidden(sampled, id))).toBe(true)
  })

  it('should append repeated samples into the same stack and separate unrelated variants', () => {
    let tree = workspace([complete(node('root'))])
    tree = applyTreeCommand(tree, { type: 'sample', nodeId: 'root', count: 2 })
    const firstGroup = tree.groups?.[0]
    if (!firstGroup) throw new Error('Expected sample group')
    const firstIds = [...firstGroup.nodeIds]
    tree = applyTreeCommand(tree, { type: 'sample', nodeId: 'root', count: 1 })
    expect(tree.groups).toHaveLength(1)
    expect(tree.groups?.[0]).toMatchObject({ id: firstGroup.id, kind: 'sample', activeNodeId: 'root' })
    expect(tree.groups?.[0].nodeIds.slice(0, firstIds.length)).toEqual(firstIds)
    expect(tree.groups?.[0].nodeIds).toHaveLength(firstIds.length + 1)

    let variantTree = workspace([complete(node('seed'))])
    variantTree = applyTreeCommand(variantTree, {
      type: 'fanOut',
      nodeId: 'seed',
      variants: [{ prompt: 'left', converters: [] }, { prompt: 'right', converters: [] }],
    })
    const variantGroup = variantTree.groups?.[0]
    if (!variantGroup) throw new Error('Expected variant group')
    const sampledVariant = applyTreeCommand(variantTree, {
      type: 'sample',
      nodeId: variantGroup.nodeIds[0] ?? '',
      count: 1,
    })
    expect(sampledVariant.groups).toHaveLength(1)
    expect(sampledVariant.groups?.[0]).toMatchObject({ kind: 'sample', activeNodeId: variantGroup.nodeIds[0] })
    expect(sampledVariant.groups?.[0].nodeIds).toHaveLength(2)
    expect(sampledVariant.groups?.[0].nodeIds).not.toContain(variantGroup.nodeIds[1])
    expect(new Set(sampledVariant.groups?.[0].nodeIds).size).toBe(sampledVariant.groups?.[0].nodeIds.length)
  })

  it('should promote an unpruned member when the active stack member is pruned', () => {
    const initial = workspace([node('root')])
    const sampled = applyTreeCommand(initial, { type: 'sample', nodeId: 'root', count: 2 })
    const group = sampled.groups?.[0]
    if (!group) throw new Error('Expected sample group')
    const pruned = applyTreeCommand(sampled, { type: 'prune', nodeId: group.activeNodeId, pruned: true })
    const replacement = pruned.groups?.[0].activeNodeId
    expect(replacement).not.toBe(group.activeNodeId)
    expect(replacement && isNodeHidden(pruned, replacement)).toBe(false)
    expect(sampled.nodes.every((item: TreeNode) => !item.pruned)).toBe(true)
  })

  it('should leave drafts unchanged when the auto-run helper rejects an incomplete child selection', () => {
    const tree = workspace([complete(node('root')), node('staged-parent', 'root'), node('new-child', 'staged-parent')])
    const before = JSON.stringify(tree)
    expect(() => getNewRunNodeIds(tree, ['new-child'])).toThrow('Run the parent first')
    expect(JSON.stringify(tree)).toBe(before)
    expect(tree.nodes[1].status).toBe('draft')
    expect(tree.nodes[2].status).toBe('draft')
  })

  it('should enforce graph capacity and per-operation fan-out limits', () => {
    const nodes = Array.from({ length: MAX_TREE_NODES }, (_: unknown, index: number) => node(`n${index}`, index ? `n${index - 1}` : null))
    expect(() => applyTreeCommand(workspace(nodes), { type: 'add', parentId: 'n299', prompt: 'over capacity' })).toThrow('maximum')
    const variants = Array.from({ length: MAX_FAN_OUT + 1 }, () => ({ prompt: 'variant', converters: [] }))
    expect(() => applyTreeCommand(workspace([node('root')]), { type: 'fanOut', nodeId: 'root', variants })).toThrow('fan-out')
    expect(() => applyTreeCommand(workspace([node('root')]), { type: 'fanOut', nodeId: 'root', variants: [] })).toThrow('fan-out')
    expect(() => applyTreeCommand(workspace([node('root')]), { type: 'childVariants', nodeId: 'root', variants })).toThrow('child variants')
    expect(() => applyTreeCommand(workspace([node('root')]), { type: 'sample', nodeId: 'root', count: MAX_FAN_OUT + 1 })).toThrow('samples')
    expect(() => applyTreeCommand(workspace(nodes), { type: 'sample', nodeId: 'n299', count: 1 })).toThrow('maximum')
  })

  it('should clear saved layout positions without mutating evidence', () => {
    const positioned = complete({ ...node('root'), position: { x: 10, y: -5 } })
    const tree = workspace([positioned, { ...node('child', 'root'), position: { x: 1, y: 2 } }])
    const reset = applyTreeCommand(tree, { type: 'autoLayout' })
    expect(reset.nodes.every((item: TreeNode) => item.position === undefined)).toBe(true)
    expect(reset.nodes[0].messages).toEqual(positioned.messages)
    expect(reset.nodes[0].attackResultId).toBe(positioned.attackResultId)
  })

  it('should allow successive comparisons without counting historical siblings against the batch limit', () => {
    let tree = workspace([node('root')])
    const variants = Array.from({ length: MAX_FAN_OUT }, () => ({ prompt: 'variant', converters: [] }))
    tree = applyTreeCommand(tree, { type: 'fanOut', nodeId: 'root', variants })
    tree = applyTreeCommand(tree, { type: 'fanOut', nodeId: 'root', variants })
    expect(tree.nodes).toHaveLength(1 + MAX_FAN_OUT * 2)
    expect(tree.nodes.every((item: TreeNode) => item.parentId === null)).toBe(true)
  })

  it('should export a portable plan without pruned branches, target details or evidence', () => {
    const tree = workspace([complete(node('root')), node('child', 'root'), { ...node('hidden', 'root'), pruned: true }, node('hidden-leaf', 'hidden')])
    const json = exportTreePlan(tree)
    const plan = JSON.parse(json)
    expect(Object.keys(plan)).toEqual(['schemaVersion', 'name', 'steps'])
    expect(plan.steps.map((item: { id: string }) => item.id)).toEqual(['root', 'child'])
    expect(json).not.toContain('conversation')
    expect(json).not.toContain('hash')
    const imported = importTreePlan(json, { ...CONFIGURATION, name: 'Imported' })
    expect(imported.name).toBe('Imported')
    expect(imported.nodes[0].id).not.toBe('root')
    expect(imported.nodes[1].parentId).toBe(imported.nodes[0].id)
    expect(imported.nodes.every((item: TreeNode) => item.status === 'draft' && !item.messages)).toBe(true)
  })

  it.each([
    ['cycle', [node('a', 'b'), node('b', 'a')]],
    ['duplicate', [node('a'), node('a')]],
    ['missing parent', [node('a', 'missing')]],
    ['draft evidence', [{ ...node('a'), conversationId: 'conversation', attackResultId: 'attack' }]],
    ['invented success', [{ ...node('a'), status: 'completed' }]],
    ['unsafe ID', [node('__proto__')]],
    ['empty prompt', [{ ...node('a'), prompt: ' ' }]],
  ])('should reject %s in snapshots', (_name: string, nodes: unknown) => {
    expect(() => parseTreeWorkspace(JSON.stringify({ ...workspace([]), nodes }))).toThrow()
  })

  it('should reject forged evidence sequences, prompt mismatches and error-piece success', () => {
    const observed = complete(node('root'))
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([{ ...observed, lastSequence: 99 }])))).toThrow('sequence')
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([{ ...observed, prompt: 'forged' }])))).toThrow('prompt')
    const errorPiece = complete(node('error'))
    if (errorPiece.messages) errorPiece.messages[1].message_pieces[0].response_error = 'blocked'
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([errorPiece])))).toThrow('error pieces')
  })

  it('should reject unknown fields, prototype pollution, credentials and excessive imports', () => {
    const plan = { schemaVersion: 1, name: 'plan', steps: [{ id: 'a', parentId: null, prompt: 'text', converters: [] }] }
    expect(() => importTreePlan(JSON.stringify({ ...plan, target: 'credential' }), CONFIGURATION)).toThrow('unsupported')
    expect(() => importTreePlan(JSON.stringify({ ...plan, steps: [{ ...plan.steps[0], status: 'completed' }] }), CONFIGURATION)).toThrow('unsupported')
    expect(() => importTreePlan('{"schemaVersion":1,"name":"plan","steps":[],"__proto__":{"polluted":true}}', CONFIGURATION)).toThrow('unsafe')
    expect(() => importTreePlan('{"steps":', CONFIGURATION)).toThrow('malformed')
    expect(() => importTreePlan(' '.repeat(5_000_001), CONFIGURATION)).toThrow('exceeds')
    expect(() => applyTreeCommand(workspace([node('root')]), {
      type: 'edit', nodeId: 'root', prompt: 'text',
      converters: [{ type: 'Converter', params: { target: { api_key: 'not-to-be-stored' } } }],
    })).toThrow('credentials')
    expect(() => createTreeWorkspace({ ...CONFIGURATION, labels: { password: 'no' } })).toThrow('credentials')
    expect(({} as Record<string, unknown>).polluted).toBeUndefined()
  })

  it('should reject cyclic, duplicate, overly nested and credential-bearing plans', () => {
    function plan(steps: unknown): string {
      return JSON.stringify({ schemaVersion: 1, name: 'Plan', steps })
    }
    const step = { id: 'a', parentId: null, prompt: 'text', converters: [] }
    expect(() => importTreePlan(plan([step, step]), CONFIGURATION)).toThrow('duplicate')
    expect(() => importTreePlan(plan([{ ...step, parentId: 'a' }]), CONFIGURATION)).toThrow('cycle')
    let nested: unknown = 'value'
    for (let index = 0; index < 40; index += 1) nested = { nested }
    expect(() => importTreePlan(plan([{
      ...step, converters: [{ type: 'Converter', params: { nested } }],
    }]), CONFIGURATION)).toThrow('nested')
    expect(() => importTreePlan(plan([{
      ...step, converters: [{ type: 'Converter', params: { children: [{ password: 'no' }] } }],
    }]), CONFIGURATION)).toThrow('credentials')
  })

  it('should reject reused evidence and scores attached to another piece', () => {
    const root = complete(node('root'))
    const sibling = { ...root, id: 'sibling', attackResultId: 'other-attack', conversationId: 'other-conversation' }
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([root, sibling])))).toThrow('multiple nodes')
    if (root.messages) {
      root.messages[1].message_pieces[0].scores = [{
        id: 'score', message_piece_id: 'wrong-piece', scorer_type: 'Scorer', score_type: 'float_scale', timestamp: TIME,
      }]
    }
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([root])))).toThrow('score evidence')
  })

  it('should validate presentation-only sizes and imported continuation lineage', () => {
    const observedRoot = complete(node('root'))
    const observedTree = workspace([observedRoot])
    const resized = applyTreeCommand(observedTree, {
      type: 'resize',
      nodeId: 'root',
      size: { width: 480, height: 440 },
      position: { x: 12, y: 24 },
    })
    expect(resized.nodes[0].size).toEqual({ width: 480, height: 440 })
    expect(resized.nodes[0].position).toEqual({ x: 12, y: 24 })
    const reset = applyTreeCommand(resized, { type: 'resize', nodeId: 'root' })
    expect(reset.nodes[0].size).toBeUndefined()
    expect(() => parseTreeWorkspace(JSON.stringify(workspace([{ ...observedRoot, size: { width: 100, height: 440 } }])))).toThrow('width')
    expect(() => applyTreeCommand(observedTree, {
      type: 'resize',
      nodeId: 'root',
      position: { x: Number.NaN, y: 0 },
    })).toThrow('position')

    const importedChild = imported(observedRoot, 'import-child', 'follow-up', 3)
    const importedLeaf = imported(importedChild, 'import-leaf', 'deeper', 5)
    const importedTree = applyTreeCommand(observedTree, {
      type: 'importContinuation',
      nodeId: 'root',
      nodes: [importedChild, importedLeaf],
    })
    expect(importedTree.nodes.map((entry: TreeNode) => entry.id)).toEqual(['root', 'import-child', 'import-leaf'])
    expect(importedTree.nodes[1]).toMatchObject({
      importedFromBackend: true,
      attackResultId: observedRoot.attackResultId,
      conversationId: observedRoot.conversationId,
      parentAttemptId: importedTree.nodes[0].attemptId,
    })
    const repeated = applyTreeCommand(importedTree, {
      type: 'importContinuation',
      nodeId: 'root',
      nodes: [importedChild, importedLeaf],
    })
    expect(repeated).toEqual(importedTree)
    expect(() => applyTreeCommand(importedTree, {
      type: 'importContinuation',
      nodeId: 'root',
      nodes: [{
        ...importedChild,
        messages: importedChild.messages?.map((entry: BackendMessage, index: number) => index === 1
          ? { ...entry, message_pieces: [{ ...entry.message_pieces[0], converted_value: 'changed reply' }] }
          : entry),
      }],
    })).toThrow('Reload before importing again')
    expect(() => parseTreeWorkspace(JSON.stringify({
      ...observedTree,
      nodes: [observedRoot, { ...importedChild, id: 'sibling-import', parentId: null, parentAttemptId: undefined }],
    }))).toThrow('contiguous imported continuations')
    const retried = applyTreeCommand(importedTree, { type: 'retry', nodeId: 'import-child', scope: 'node' })
    expect(() => parseTreeWorkspace(JSON.stringify(retried))).not.toThrow()
    expect(retried.nodes.find((entry: TreeNode) => entry.id === 'import-child')).toMatchObject({ status: 'draft', importedFromBackend: true })
  })
})
