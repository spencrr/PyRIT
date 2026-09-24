import { attacksApi } from '@/services/api'
import type { AttackSummary, BackendMessage, TreeNode, TreeWorkspace } from '@/types'

import { discoverTreeContinuation } from './treeHistory'
import { applyTreeCommand, createTreeWorkspace, getCurrentAttemptId, parseTreeWorkspace } from './treeModel'

jest.mock('@/services/api', () => ({
  attacksApi: { getAttack: jest.fn(), getMessages: jest.fn() },
}))

const TIME = '2026-09-12T12:00:00.000Z'
const CONFIGURATION = {
  name: 'Tree', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: 'System prompt', labels: {},
}

function node(id: string, parentId: string | null = null): TreeNode {
  return { id, parentId, prompt: id, converters: [], status: 'draft', kept: false, pruned: false }
}

function message(role: string, turn: number, content: string, prefix: string, error = 'none'): BackendMessage {
  return {
    role, turn_number: turn, created_at: TIME,
    message_pieces: [{
      id: `${prefix}-${turn}`, original_value_data_type: 'text', converted_value_data_type: 'text',
      original_value: content, converted_value: content, scores: [], response_error: error,
    }],
  }
}

function complete(input: TreeNode, start = 1): TreeNode {
  return {
    ...input,
    status: 'completed',
    attackResultId: `${input.id}-attack`,
    conversationId: `${input.id}-conversation`,
    lastSequence: start + 1,
    messages: [
      message('user', start, input.prompt, input.id),
      message('assistant', start + 1, `${input.prompt}-reply`, input.id),
    ],
  }
}

function workspace(nodes: TreeNode[]): TreeWorkspace {
  return parseTreeWorkspace(JSON.stringify({ ...createTreeWorkspace(CONFIGURATION), nodes }))
}

function attackSummary(root: TreeNode): AttackSummary {
  return {
    attack_result_id: root.attackResultId ?? 'missing-attack',
    conversation_id: root.conversationId ?? 'missing-conversation',
    attack_type: 'Tree',
    objective: 'Tree',
    target: { target_type: 'Target', target_registry_name: 'target', identifier_hash: CONFIGURATION.targetIdentifierHash },
    converters: [],
    outcome: 'undetermined',
    message_count: 0,
    related_conversation_ids: [],
    labels: {},
    created_at: TIME,
    updated_at: TIME,
  }
}

describe('treeHistory', () => {
  const getAttack = jest.mocked(attacksApi.getAttack)
  const getMessages = jest.mocked(attacksApi.getMessages)

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('allows further imports after backend scoring without rewriting saved evidence', async () => {
    const tree = workspace([complete(node('root'))])
    const root = tree.nodes[0]
    getAttack.mockResolvedValue(attackSummary(root))
    const firstTurn = [message('user', 3, 'First follow-up', 'history'), message('assistant', 4, 'First reply', 'history')]
    getMessages.mockResolvedValue({ conversation_id: root.conversationId ?? '', messages: [...(root.messages ?? []), ...firstTurn] })
    const initial = await discoverTreeContinuation(tree, root.id)
    const imported = applyTreeCommand(tree, { type: 'importContinuation', nodeId: root.id, nodes: initial.nodes })
    const scoredReply = { ...firstTurn[1], message_pieces: firstTurn[1].message_pieces.map((piece) => ({
      ...piece, scores: [{ id: 'score', message_piece_id: piece.id, scorer_type: 'SubStringScorer', score_type: 'true_false', score_value: 'true', timestamp: TIME }],
    })) }
    getMessages.mockResolvedValue({ conversation_id: root.conversationId ?? '', messages: [
      ...(root.messages ?? []), firstTurn[0], scoredReply,
      message('user', 5, 'Second follow-up', 'history'), message('assistant', 6, 'Second reply', 'history'),
    ] })
    const next = await discoverTreeContinuation(imported, root.id)
    expect(next.nodes).toHaveLength(1)
    expect(next.nodes[0]).toMatchObject({ parentId: initial.nodes[0].id, prompt: 'Second follow-up' })
    expect(imported.nodes[1].messages?.[1].message_pieces[0].scores).toEqual([])
  })

  it('discovers deterministic imported continuation nodes and counts a trailing pending user message', async () => {
    const tree = workspace([complete(node('root'))])
    const root = tree.nodes[0]
    getAttack.mockResolvedValue(attackSummary(root))
    getMessages.mockResolvedValue({
      conversation_id: root.conversationId ?? '',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'system'),
        ...(root.messages ?? []),
        message('user', 3, 'follow up', 'history'),
        message('assistant', 4, 'follow up reply', 'history'),
        message('user', 5, 'second follow up', 'history'),
        message('assistant', 6, 'second follow up reply', 'history'),
        message('user', 7, 'pending question', 'history'),
      ],
    })
    const discovered = await discoverTreeContinuation(tree, 'root')
    expect(discovered).toMatchObject({
      workspaceId: tree.id,
      nodeId: 'root',
      attemptId: getCurrentAttemptId(root),
      pendingMessages: 1,
    })
    expect(discovered.nodes).toHaveLength(2)
    expect(discovered.nodes[0]).toMatchObject({
      parentId: 'root',
      parentAttemptId: getCurrentAttemptId(root),
      importedFromBackend: true,
      prompt: 'follow up',
      attackResultId: root.attackResultId,
      conversationId: root.conversationId,
    })
    expect(discovered.nodes[1]).toMatchObject({
      parentId: discovered.nodes[0].id,
      parentAttemptId: discovered.nodes[0].attemptId,
      importedFromBackend: true,
      prompt: 'second follow up',
    })
    await expect(discoverTreeContinuation(tree, 'root')).resolves.toEqual(discovered)
  })

  it('continues from the deepest existing imported chain instead of recreating imported prefixes', async () => {
    const initialTree = workspace([complete(node('root'))])
    const root = initialTree.nodes[0]
    getAttack.mockResolvedValue(attackSummary(root))
    getMessages.mockResolvedValue({
      conversation_id: root.conversationId ?? '',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'system'),
        ...(root.messages ?? []),
        message('user', 3, 'follow up', 'history'),
        message('assistant', 4, 'follow up reply', 'history'),
        message('user', 5, 'second follow up', 'history'),
        message('assistant', 6, 'second follow up reply', 'history'),
      ],
    })
    const firstImport = await discoverTreeContinuation(initialTree, 'root')
    const importedTree = applyTreeCommand(initialTree, {
      type: 'importContinuation',
      nodeId: 'root',
      nodes: [firstImport.nodes[0]],
    })
    const appended = await discoverTreeContinuation(importedTree, 'root')
    expect(appended.nodes).toHaveLength(1)
    expect(appended.nodes[0]?.parentId).toBe(firstImport.nodes[0]?.id)
    expect(appended.nodes[0]?.prompt).toBe('second follow up')
  })

  it.each([
    ['missing prefix', []],
    ['changed prefix', [message('system', 0, CONFIGURATION.systemPrompt, 'system'), message('user', 1, 'changed', 'history')]],
  ])('rejects %s when the saved source turn no longer matches backend history', async (_name: string, prefix: BackendMessage[]) => {
    const tree = workspace([complete(node('root'))])
    const root = tree.nodes[0]
    getAttack.mockResolvedValue(attackSummary(root))
    getMessages.mockResolvedValue({
      conversation_id: root.conversationId ?? '',
      messages: prefix,
    })
    await expect(discoverTreeContinuation(tree, 'root')).rejects.toThrow('Saved node history no longer matches')
  })

  it('rejects source identity drift and conflicting existing imported nodes', async () => {
    const tree = workspace([complete(node('root'))])
    const root = tree.nodes[0]
    getAttack.mockResolvedValue({ ...attackSummary(root), target: { target_type: 'Target', identifier_hash: 'drifted' } })
    await expect(discoverTreeContinuation(tree, 'root')).rejects.toThrow('Target identity changed')

    getAttack.mockResolvedValue(attackSummary(root))
    getMessages.mockResolvedValue({
      conversation_id: root.conversationId ?? '',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'system'),
        ...(root.messages ?? []),
        message('user', 3, 'follow up', 'history'),
        message('assistant', 4, 'follow up reply', 'history'),
      ],
    })
    const imported = await discoverTreeContinuation(tree, 'root')
    const corrupted = applyTreeCommand(tree, {
      type: 'importContinuation',
      nodeId: 'root',
      nodes: [{
        ...imported.nodes[0],
        messages: imported.nodes[0].messages?.map((entry: BackendMessage, index: number) => index === 1
          ? { ...entry, message_pieces: [{ ...entry.message_pieces[0], converted_value: 'tampered reply' }] }
          : entry),
      }],
    })
    await expect(discoverTreeContinuation(corrupted, 'root')).rejects.toThrow('Reload before importing again')
  })

  it('rejects unsupported continuation shapes and unrelated backend conversations', async () => {
    const tree = workspace([complete(node('root'))])
    const root = tree.nodes[0]
    getAttack.mockResolvedValue(attackSummary(root))
    getMessages.mockResolvedValue({
      conversation_id: 'other-conversation',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'system'),
        ...(root.messages ?? []),
      ],
    })
    await expect(discoverTreeContinuation(tree, 'root')).rejects.toThrow('unrelated conversation')

    getMessages.mockResolvedValue({
      conversation_id: root.conversationId ?? '',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'system'),
        ...(root.messages ?? []),
        {
          role: 'user',
          turn_number: 3,
          created_at: TIME,
          message_pieces: [
            {
              id: 'multi-1', original_value_data_type: 'text', converted_value_data_type: 'text',
              original_value: 'first', converted_value: 'first', scores: [], response_error: 'none',
            },
            {
              id: 'multi-2', original_value_data_type: 'text', converted_value_data_type: 'text',
              original_value: 'second', converted_value: 'second', scores: [], response_error: 'none',
            },
          ],
        },
      ],
    })
    await expect(discoverTreeContinuation(tree, 'root')).rejects.toThrow('single-piece user continuation turns')
  })
})
