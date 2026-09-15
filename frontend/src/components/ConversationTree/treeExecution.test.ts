import { waitFor } from '@testing-library/react'

import { attacksApi, convertersApi, targetsApi } from '@/services/api'
import type {
  AddMessageRequest, AddMessageResponse, BackendMessage, ConverterInstance, CreateAttackRequest,
  TargetInstance, TreeNode, TreeScoreRun, TreeWorkspace,
} from '@/types'

import { recoverTreeNode, runTree, TreePersistenceError } from './treeExecution'
import {
  applyTreeCommand, createTreeWorkspace, getRunNodeIds, getTreeLabels, getTreeSettings, MAX_RUN_CALLS, parseTreeWorkspace,
  TREE_NODE_LABEL, TREE_WORKSPACE_LABEL,
} from './treeModel'

jest.mock('@/services/api', () => ({
  attacksApi: { createAttack: jest.fn(), getAttack: jest.fn(), getMessages: jest.fn(), createConversation: jest.fn(), addMessage: jest.fn() },
  convertersApi: { createConverter: jest.fn(), getConverter: jest.fn() },
  targetsApi: { getTarget: jest.fn() },
}))

const TIME = '2026-09-12T12:00:00.000Z'
const CONFIGURATION = {
  name: 'Tree', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: 'System prompt',
  labels: { operator: 'tester', campaign: 'tree' },
}
const TARGET: TargetInstance = {
  target_registry_name: 'target', identifier: { class_name: 'OpenAIChatTarget', class_module: 'pyrit.prompt_target.openai.openai_chat_target', hash: 'hash' },
  capabilities: {
    supports_multi_turn: true, supports_editable_history: true, supports_system_prompt: true,
    supports_json_schema: false, supports_json_output: false,
    supported_input_modalities: ['text'], supported_output_modalities: ['text'],
  },
}
const CONVERTER: ConverterInstance = {
  converter_id: 'converter-id',
  identifier: {
    class_name: 'Base64Converter', class_module: 'pyrit.converter', hash: 'converter-hash', pyrit_version: '1',
    supported_input_types: ['text'], supported_output_types: ['text'],
  },
}

function node(id: string, parentId: string | null = null): TreeNode {
  return { id, parentId, prompt: id, converters: [], status: 'draft', kept: false, pruned: false }
}

function workspace(nodes: TreeNode[]): TreeWorkspace {
  return { ...createTreeWorkspace(CONFIGURATION), nodes }
}

function configuredWorkspace(nodes: TreeNode[], settings: Partial<ReturnType<typeof getTreeSettings>>): TreeWorkspace {
  return applyTreeCommand(workspace(nodes), {
    type: 'settings',
    settings: { ...getTreeSettings(workspace([])), ...settings },
  })
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

function response(attackId: string, conversationId: string, messages: BackendMessage[]): AddMessageResponse {
  return {
    attack: {
      attack_result_id: attackId, conversation_id: conversationId, attack_type: 'Tree', objective: 'Tree',
      converters: [], message_count: messages.length, related_conversation_ids: [], labels: CONFIGURATION.labels,
      created_at: TIME, updated_at: TIME, outcome: 'undetermined',
      target: { target_type: 'Target', target_registry_name: 'target', identifier_hash: 'hash' },
    },
    messages: { conversation_id: conversationId, messages },
  }
}

function scoreRun(node: TreeNode): TreeScoreRun {
  const pieceId = node.messages?.[node.messages.length - 1]?.message_pieces[0]?.id ?? `${node.id}-piece`
  return {
    id: `score-run-${node.id}`,
    scorerId: 'judge',
    scorerHash: 'judge-hash',
    status: 'complete',
    scores: [{
      id: `score-${node.id}`,
      message_piece_id: pieceId,
      scorer_type: 'Judge',
      score_type: 'float_scale',
      score_value: '0.5',
      timestamp: TIME,
    }],
  }
}

function withScore(tree: TreeWorkspace, nodeId: string): TreeWorkspace {
  const current = tree.nodes.find((entry: TreeNode) => entry.id === nodeId)
  if (!current) throw new Error('Missing score target')
  return applyTreeCommand(tree, {
    type: 'score',
    nodeId,
    attemptId: current.attemptId ?? '',
    result: scoreRun(current),
  })
}

describe('treeExecution', () => {
  const createAttack = jest.mocked(attacksApi.createAttack)
  const addMessage = jest.mocked(attacksApi.addMessage)
  const getAttack = jest.mocked(attacksApi.getAttack)
  const getMessages = jest.mocked(attacksApi.getMessages)
  const getTarget = jest.mocked(targetsApi.getTarget)
  const createConverter = jest.mocked(convertersApi.createConverter)
  const getConverter = jest.mocked(convertersApi.getConverter)
  let snapshots: TreeWorkspace[]
  let stop: boolean
  let save: jest.Mock<Promise<TreeWorkspace>, [TreeWorkspace]>
  let onUpdate: jest.Mock<void, [TreeWorkspace]>
  let requests: CreateAttackRequest[]

  function options(nodeIds: string[]): Parameters<typeof runTree>[1] {
    return { nodeIds, save, onUpdate, isStopped: (): boolean => stop }
  }

  beforeEach(() => {
    jest.resetAllMocks()
    snapshots = []
    stop = false
    requests = []
    save = jest.fn(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      const saved = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(saved)
      return saved
    })
    onUpdate = jest.fn()
    getTarget.mockResolvedValue(TARGET)
    createAttack.mockImplementation(async (request: CreateAttackRequest) => {
      requests.push(request)
      return { attack_result_id: `attack-${requests.length}`, conversation_id: `conversation-${requests.length}`, created_at: TIME }
    })
    createConverter.mockResolvedValue({ converter_id: 'converter-id', converter_type: 'Base64Converter' })
    getConverter.mockResolvedValue(CONVERTER)
    getAttack.mockImplementation(async (attackId: string) => response(attackId, attackId.replace('attack', 'conversation'), []).attack)
    getMessages.mockImplementation(async (_attackId: string, conversationId: string) => {
      const saved = snapshots[snapshots.length - 1]
      const current = saved.nodes.find((item: TreeNode) => item.conversationId === conversationId)
      const ancestors: BackendMessage[][] = []
      let parentId = current?.parentId
      while (parentId) {
        const parent = saved.nodes.find((item: TreeNode) => item.id === parentId)
        ancestors.unshift(parent?.messages ?? [])
        parentId = parent?.parentId
      }
      return {
        conversation_id: conversationId,
        messages: [
          ...(saved.systemPrompt ? [message('system', 0, saved.systemPrompt, 'cloned-system')] : []),
          ...ancestors.flat().map((item: BackendMessage) => ({
            ...item, role: item.role === 'assistant' ? 'simulated_assistant' : item.role,
          })),
        ],
      }
    })
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      const index = Number(attackId.split('-')[1]) - 1
      const turn = (requests[index].cutoff_index ?? 0) + 1
      const history = requests[index].source_conversation_id
        ? [message('system', 0, 'System prompt', attackId), message('user', 1, 'parent', attackId), message('simulated_assistant', 2, 'old response', attackId)]
        : [message('system', 0, 'System prompt', attackId)]
      return response(attackId, request.target_conversation_id, [
        ...history,
        message('user', turn, request.pieces[0].original_value, attackId),
        message('assistant', turn + 1, 'observed response', attackId),
      ])
    })
  })

  it('should execute sequential independent attacks with the exact observed parent cutoff', async () => {
    const tree = workspace([node('root'), node('left', 'root'), node('right', 'root')])
    tree.nodes[1].converters = [{ type: 'Base64Converter', params: { prefix: 'value' } }]
    const result = await runTree(tree, options(getRunNodeIds(tree)))
    expect(result.nodes.map((item: TreeNode) => item.status)).toEqual(['completed', 'completed', 'completed'])
    expect(createAttack.mock.calls.map((call: [CreateAttackRequest]) => call[0])).toEqual([
      { target_registry_name: 'target', name: 'Tree', labels: getTreeLabels(tree, 'root'), system_prompt: 'System prompt' },
      { target_registry_name: 'target', name: 'Tree', labels: getTreeLabels(tree, 'left'), source_conversation_id: 'conversation-1', cutoff_index: 2 },
      { target_registry_name: 'target', name: 'Tree', labels: getTreeLabels(tree, 'right'), source_conversation_id: 'conversation-1', cutoff_index: 2 },
    ])
    expect(attacksApi.createConversation).not.toHaveBeenCalled()
    expect(addMessage).toHaveBeenNthCalledWith(2, 'attack-2', {
      role: 'user', pieces: [{ data_type: 'text', original_value: 'left' }], send: true,
      target_registry_name: 'target', target_conversation_id: 'conversation-2',
      converter_ids: ['converter-id'], labels: getTreeLabels(tree, 'left'),
    })
    expect(createConverter).toHaveBeenCalledWith({ type: 'Base64Converter', params: { prefix: 'value' } })
    expect(getConverter).toHaveBeenCalledWith('converter-id')
    expect(result.nodes[1].messages?.map((item: BackendMessage) => item.turn_number)).toEqual([3, 4])
    expect(result.nodes[2].lastSequence).toBe(4)
    expect(tree.nodes[0].status).toBe('draft')
    expect(onUpdate).toHaveBeenCalledTimes(snapshots.length)
    expect(onUpdate).toHaveBeenLastCalledWith(result)
  })

  it('should add stable reserved labels to new requests for legacy workspaces without mutating saved labels', async () => {
    const legacy = parseTreeWorkspace(JSON.stringify({
      ...workspace([node('root')]),
      labels: { operator: 'tester', campaign: 'tree', [TREE_NODE_LABEL]: 'stale-node' },
    }))
    const result = await runTree(legacy, options(['root']))
    expect(createAttack).toHaveBeenCalledWith({
      target_registry_name: 'target',
      name: 'Tree',
      labels: getTreeLabels(legacy, 'root'),
      system_prompt: 'System prompt',
    })
    expect(addMessage).toHaveBeenCalledWith('attack-1', {
      role: 'user',
      pieces: [{ data_type: 'text', original_value: 'root' }],
      send: true,
      target_registry_name: 'target',
      target_conversation_id: 'conversation-1',
      converter_ids: [],
      labels: getTreeLabels(legacy, 'root'),
    })
    expect(result.labels).toEqual(legacy.labels)
    expect(result.labels[TREE_WORKSPACE_LABEL]).toBeUndefined()
  })

  it('should save running before any API and IDs before sending, and completion before the next node', async () => {
    getTarget.mockImplementation(async () => {
      expect(snapshots[snapshots.length - 1].nodes.some((item: TreeNode) => item.status === 'running')).toBe(true)
      return TARGET
    })
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      const saved = snapshots[snapshots.length - 1]
      const running = saved.nodes.find((item: TreeNode) => item.status === 'running')
      expect(running).toMatchObject({ attackResultId: attackId, conversationId: request.target_conversation_id })
      return response(attackId, request.target_conversation_id, [
        message('user', 5, request.pieces[0].original_value, attackId),
        message('assistant', 9, 'response', attackId),
      ])
    })
    createAttack.mockImplementation(async (request: CreateAttackRequest) => {
      if (request.source_conversation_id) {
        expect(snapshots[snapshots.length - 1].nodes[0].status).toBe('completed')
        expect(request.cutoff_index).toBe(9)
      }
      requests.push(request)
      return { attack_result_id: `attack-${requests.length}`, conversation_id: `conversation-${requests.length}`, created_at: TIME }
    })
    await runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))
    expect(addMessage).toHaveBeenCalledTimes(2)
  })

  it.each(['hash', 'multi-turn', 'history', 'text', 'system'])('should refuse incompatible targets: %s', async (reason: string) => {
    const target = JSON.parse(JSON.stringify(TARGET)) as TargetInstance
    if (reason === 'hash') target.identifier.hash = 'drifted'
    if (target.capabilities) {
      if (reason === 'multi-turn') target.capabilities.supports_multi_turn = false
      if (reason === 'history') target.capabilities.supports_editable_history = undefined
      if (reason === 'text') target.capabilities.supported_input_modalities = ['image']
      if (reason === 'system') target.capabilities.supports_system_prompt = false
    }
    getTarget.mockResolvedValue(target)
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(result.nodes[0].error).toContain('Inspect backend history before retrying')
    expect(createAttack).not.toHaveBeenCalled()
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should reject duplicates, hidden nodes, wrong order, observed nodes and oversized queues before I/O', async () => {
    const tree = workspace([node('root'), node('child', 'root'), { ...node('hidden'), pruned: true }])
    for (const ids of [['root', 'root'], ['hidden'], ['child', 'root'], ['missing']]) {
      await expect(runTree(tree, options(ids))).rejects.toThrow()
    }
    await expect(runTree(workspace([{ ...node('running'), status: 'running' }]), options(['running']))).rejects.toThrow('draft')
    const expensive = workspace([{
      ...node('root'), converters: Array.from({ length: MAX_RUN_CALLS }, () => ({ type: 'Converter', params: {} })),
    }])
    await expect(runTree(expensive, options(['root']))).rejects.toThrow('budget')
    expect(save).not.toHaveBeenCalled()
    expect(getTarget).not.toHaveBeenCalled()
    expect(createAttack).not.toHaveBeenCalled()
  })

  it('should retain current-turn error pieces as errors and stop before descendants', async () => {
    addMessage.mockResolvedValue(response('attack-1', 'conversation-1', [
      message('system', 0, 'System prompt', 'attack-1'),
      message('user', 1, 'root', 'attack-1'),
      message('assistant', 2, 'blocked evidence', 'attack-1', 'blocked'),
    ]))
    const result = await runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))
    expect(result.nodes[0]).toMatchObject({ status: 'error', conversationId: 'conversation-1', lastSequence: 2 })
    expect(result.nodes[0].messages?.[1].message_pieces[0].converted_value).toBe('blocked evidence')
    expect(result.nodes[1].status).toBe('draft')
    expect(addMessage).toHaveBeenCalledTimes(1)
  })

  it('should not claim completion for a missing assistant, and should retain observed user evidence', async () => {
    addMessage.mockResolvedValue(response('attack-1', 'conversation-1', [message('user', 1, 'root', 'attack-1')]))
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(result.nodes[0].messages).toHaveLength(1)
  })

  it.each([
    { isAxiosError: true, message: 'network', config: { headers: { Authorization: 'do-not-save' } } },
    { isAxiosError: true, code: 'ECONNABORTED' },
    { isAxiosError: true, response: { status: 500, data: { detail: 'do-not-save api_key' } } },
  ])('should never retry an ambiguous or failed send and retain created IDs', async (failure: unknown) => {
    addMessage.mockRejectedValue(failure)
    const result = await runTree(workspace([node('root'), node('other')]), options(['root', 'other']))
    expect(result.nodes[0]).toMatchObject({
      status: 'error', attackResultId: 'attack-1', conversationId: 'conversation-1',
      error: expect.stringContaining('Inspect backend history before retrying'),
    })
    expect(JSON.stringify(result)).not.toContain('do-not-save')
    expect(result.nodes[1].status).toBe('draft')
    await expect(runTree(result, options(['root']))).rejects.toThrow('draft')
    expect(addMessage).toHaveBeenCalledTimes(1)
  })

  it('should fail visibly on converter creation failure without sending', async () => {
    createConverter.mockRejectedValue(new Error('failed'))
    const result = await runTree(workspace([{ ...node('root'), converters: [{ type: 'Converter', params: {} }] }]), options(['root']))
    expect(result.nodes[0]).toMatchObject({ status: 'error', attackResultId: 'attack-1' })
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should persist errors for target lookup and attack creation failures', async () => {
    getTarget.mockRejectedValueOnce(new Error('failed'))
    expect((await runTree(workspace([node('root')]), options(['root']))).nodes[0].status).toBe('error')
    createAttack.mockRejectedValueOnce(new Error('failed'))
    expect((await runTree(workspace([node('root')]), options(['root']))).nodes[0].status).toBe('error')
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should not start when already stopped and stop between creation and sending', async () => {
    stop = true
    await runTree(workspace([node('root')]), options(['root']))
    expect(save).not.toHaveBeenCalled()
    stop = false
    createAttack.mockImplementation(async () => {
      stop = true
      return { attack_result_id: 'attack-1', conversation_id: 'conversation-1', created_at: TIME }
    })
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0]).toMatchObject({ status: 'error', attackResultId: 'attack-1', error: expect.stringContaining('Stopped') })
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should preserve an in-flight response when stop is requested and skip the next node', async () => {
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      stop = true
      return response(attackId, request.target_conversation_id, [
        message('user', 1, request.pieces[0].original_value, attackId), message('assistant', 2, 'answer', attackId),
      ])
    })
    const result = await runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))
    expect(result.nodes[0].status).toBe('completed')
    expect(result.nodes[1].status).toBe('draft')
    expect(addMessage).toHaveBeenCalledTimes(1)
  })

  it.each([1, 2, 3])('should propagate save failure %s without masking it or continuing execution', async (failingSave: number) => {
    let count = 0
    save.mockImplementation(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      count += 1
      if (count === failingSave) throw new Error('Storage revision conflict')
      const saved = { ...tree, revision: tree.revision + 1 }
      snapshots.push(saved)
      return saved
    })
    await expect(runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))).rejects.toThrow('Storage revision conflict')
    expect(addMessage).toHaveBeenCalledTimes(failingSave === 3 ? 1 : 0)
    expect(save).toHaveBeenCalledTimes(failingSave)
  })

  it('should reject a response for a different conversation rather than attach its evidence', async () => {
    addMessage.mockResolvedValue(response('wrong-attack', 'wrong-conversation', [
      message('user', 1, 'root', 'other'), message('assistant', 2, 'reply', 'other'),
    ]))
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(result.nodes[0].messages).toBeUndefined()
    expect(result.nodes[0].error).toContain('inconsistent')
  })

  it('should project real backend DTO extensions while preserving scores and converted evidence', async () => {
    const reply = message('assistant', 2, 'reply', 'attack-1')
    reply.message_pieces[0].prompt_metadata = { finish_reason: 'stop', reasoning: ['observed'] }
    reply.message_pieces[0].scores = [{
      id: 'score', message_piece_id: 'attack-1-2', scorer_type: 'Scorer', score_type: 'float_scale',
      score_value: '0.5', status: 'completed', is_objective_score: true,
      score_category: ['category'], score_rationale: 'observed rationale', timestamp: TIME,
    }]
    const user = message('user', 1, 'root', 'attack-1')
    user.message_pieces[0].converted_value = 'converted prompt'
    Object.assign(user.message_pieces[0], {
      conversation_id: 'conversation-1', sequence: 1, original_value_sha256: 'sha',
      converter_identifiers: [{ class_name: 'Converter', params: { api_key: 'not-for-storage' } }],
    })
    addMessage.mockResolvedValue(response('attack-1', 'conversation-1', [user, reply]))
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('completed')
    expect(result.nodes[0].messages?.[0].message_pieces[0].converted_value).toBe('converted prompt')
    expect(result.nodes[0].messages?.[1]).toEqual(reply)
    expect(JSON.stringify(result)).not.toContain('not-for-storage')
  })

  it('should recheck target identity before another node is prepared', async () => {
    getTarget.mockResolvedValueOnce(TARGET).mockResolvedValueOnce({
      ...TARGET, identifier: { ...TARGET.identifier, hash: 'different' },
    })
    const result = await runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))
    expect(result.nodes.map((item: TreeNode) => item.status)).toEqual(['completed', 'error'])
    expect(addMessage).toHaveBeenCalledTimes(1)
  })

  it('should stop between converter calls without invoking the remaining pipeline or target', async () => {
    createConverter.mockImplementation(async () => {
      stop = true
      return { converter_id: 'converter', converter_type: 'Converter' }
    })
    const tree = workspace([{
      ...node('root'), converters: [{ type: 'FirstConverter', params: {} }, { type: 'SecondConverter', params: {} }],
    }])
    const result = await runTree(tree, options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(createConverter).toHaveBeenCalledTimes(1)
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should save a protocol error when attack creation returns incomplete identifiers', async () => {
    createAttack.mockResolvedValue({ attack_result_id: 'attack', conversation_id: '', created_at: TIME })
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should preserve standalone error evidence instead of inventing a successful turn', async () => {
    addMessage.mockResolvedValue(response('attack-1', 'conversation-1', [message('assistant', 1, 'failure', 'attack-1', 'processing')]))
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0].status).toBe('error')
    expect(result.nodes[0].messages?.[0].message_pieces[0].converted_value).toBe('failure')
  })

  it('should reject target drift during attack creation before any model send', async () => {
    getAttack.mockResolvedValue({
      ...response('attack-1', 'conversation-1', []).attack,
      target: { target_type: 'Target', identifier_hash: 'drifted' },
    })
    const result = await runTree(workspace([node('root')]), options(['root']))
    expect(result.nodes[0]).toMatchObject({ status: 'error', attackResultId: 'attack-1', conversationId: 'conversation-1' })
    expect(result.nodes[0].error).toContain('created attack target')
    expect(addMessage).not.toHaveBeenCalled()
  })

  it.each([
    { supported_input_types: ['image_path'], supported_output_types: ['text'] },
    { supported_input_types: null, supported_output_types: ['text'] },
    { supported_input_types: ['text'], supported_output_types: ['image_path'] },
    { supported_input_types: ['text'], supported_output_types: ['text', 'image_path'] },
    { supported_input_types: ['text'], supported_output_types: [] },
    { supported_input_types: ['text'], supported_output_types: undefined },
  ])('should reject unsupported or unknown converter capabilities before any model send: %j', async (
    capabilities: Pick<ConverterInstance['identifier'], 'supported_input_types' | 'supported_output_types'>,
  ) => {
    getConverter.mockResolvedValue({ ...CONVERTER, identifier: { ...CONVERTER.identifier, ...capabilities } })
    const tree = workspace([{
      ...node('root'), converters: [{ type: 'Base64Converter', params: {} }],
    }, node('child', 'root')])
    const result = await runTree(tree, options(['root', 'child']))
    expect(result.nodes[0]).toMatchObject({
      status: 'error', conversationId: 'conversation-1', error: expect.stringContaining('exclusively text output'),
    })
    expect(result.nodes[1].status).toBe('draft')
    expect(onUpdate).toHaveBeenLastCalledWith(result)
    expect(snapshots[snapshots.length - 1]).toEqual(result)
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should reject converter identity drift and lookup failures without sending', async () => {
    const tree = workspace([{ ...node('root'), converters: [{ type: 'Base64Converter', params: {} }] }])
    getConverter.mockResolvedValueOnce({ ...CONVERTER, converter_id: 'unrelated-converter' })
    const drifted = await runTree(tree, options(['root']))
    expect(drifted.nodes[0].error).toContain('converter identity')
    getConverter.mockRejectedValueOnce({ isAxiosError: true })
    const unavailable = await runTree(tree, options(['root']))
    expect(unavailable.nodes[0].error).toContain('Unable to verify converter capabilities')
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should stop after converter capability lookup and before conversion or target sending', async () => {
    getConverter.mockImplementation(async () => {
      stop = true
      return CONVERTER
    })
    const result = await runTree(
      workspace([{ ...node('root'), converters: [{ type: 'Base64Converter', params: {} }] }]), options(['root']),
    )
    expect(result.nodes[0].status).toBe('error')
    expect(result.nodes[0].error).toContain('Stopped')
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should preserve a completed parent and send only selected draft descendants', async () => {
    const completed = await runTree(workspace([node('root'), node('child', 'root'), node('unrelated')]), options(['root']))
    const rootSnapshot = JSON.stringify(completed.nodes[0])
    const queue = getRunNodeIds(completed, 'root')
    expect(queue).toEqual(['child'])
    const result = await runTree(completed, options(queue))
    expect(JSON.stringify(result.nodes[0])).toBe(rootSnapshot)
    expect(result.nodes[1].status).toBe('completed')
    expect(result.nodes[2].status).toBe('draft')
    expect(createAttack).toHaveBeenLastCalledWith(expect.objectContaining({
      source_conversation_id: completed.nodes[0].conversationId, cutoff_index: completed.nodes[0].lastSequence,
    }))
  })

  it('should merge execution updates into the latest workspace without overwriting unrelated edits', async () => {
    let latest = parseTreeWorkspace(JSON.stringify(workspace([node('root'), node('other')])))
    const saveLatest = jest.fn(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      latest = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(latest)
      return latest
    })
    addMessage.mockImplementationOnce(async (attackId: string, request: AddMessageRequest) => {
      latest = parseTreeWorkspace(JSON.stringify({
        ...latest,
        revision: latest.revision + 1,
        settings: { ...getTreeSettings(latest), markdown: true },
        nodes: latest.nodes.map((item: TreeNode) => item.id === 'other' ? { ...item, prompt: 'edited while running' } : item),
      }))
      return response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', 1, request.pieces[0].original_value, attackId),
        message('assistant', 2, 'observed response', attackId),
      ])
    })
    const result = await runTree(latest, {
      nodeIds: ['root'],
      save: saveLatest,
      onUpdate,
      getLatest: (): TreeWorkspace => latest,
      isStopped: (): boolean => false,
    })
    expect(result.nodes.find((item: TreeNode) => item.id === 'root')?.status).toBe('completed')
    expect(result.nodes.find((item: TreeNode) => item.id === 'other')?.prompt).toBe('edited while running')
    expect(getTreeSettings(result).markdown).toBe(true)
  })

  it('should skip a queued node whose draft changes before dispatch and never send it', async () => {
    let latest = parseTreeWorkspace(JSON.stringify(workspace([node('first'), node('second')])))
    const saveLatest = jest.fn(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      latest = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(latest)
      return latest
    })
    const queue = getRunNodeIds(latest)
    const result = await runTree(latest, {
      nodeIds: queue,
      save: saveLatest,
      onUpdate,
      getLatest: (): TreeWorkspace => latest,
      isStopped: (): boolean => false,
      onNodeCompleted: async (saved: TreeWorkspace, nodeId: string): Promise<void> => {
        if (nodeId !== 'first') return
        latest = parseTreeWorkspace(JSON.stringify({
          ...saved,
          revision: saved.revision + 1,
          nodes: saved.nodes.map((item: TreeNode) => item.id === 'second' ? { ...item, prompt: 'edited second' } : item),
        }))
      },
    })
    expect(addMessage).toHaveBeenCalledTimes(1)
    expect(createAttack).toHaveBeenCalledTimes(1)
    expect(result.nodes.find((item: TreeNode) => item.id === 'first')?.status).toBe('completed')
    expect(result.nodes.find((item: TreeNode) => item.id === 'second')).toMatchObject({ status: 'draft', prompt: 'edited second' })
  })

  it('should safely skip a node when commit-node-update detects a stale queued prompt before sending', async () => {
    let latest = parseTreeWorkspace(JSON.stringify(workspace([node('first'), node('second')])))
    const queue = getRunNodeIds(latest)
    const commitNodeUpdate = jest.fn(async (nodeId: string, expected: TreeNode, update: Partial<TreeNode>): Promise<TreeWorkspace> => {
      if (nodeId === 'first' && update.status === 'running') {
        latest = parseTreeWorkspace(JSON.stringify({
          ...latest,
          revision: latest.revision + 1,
          nodes: latest.nodes.map((item: TreeNode) => item.id === 'first' ? { ...item, prompt: 'edited before run' } : item),
        }))
        throw new Error('Attempt changed before execution update; stopped without overwriting edits.')
      }
      latest = parseTreeWorkspace(JSON.stringify({
        ...latest,
        revision: latest.revision + 1,
        nodes: latest.nodes.map((item: TreeNode) => item.id === nodeId ? { ...item, ...update } : item),
      }))
      snapshots.push(latest)
      onUpdate(latest)
      return latest
    })
    const result = await runTree(latest, {
      nodeIds: queue,
      save,
      onUpdate,
      getLatest: (): TreeWorkspace => latest,
      isStopped: (): boolean => false,
      commitNodeUpdate,
    })
    expect(createAttack).toHaveBeenCalledTimes(1)
    expect(addMessage).toHaveBeenCalledTimes(1)
    expect(result.nodes.find((item: TreeNode) => item.id === 'first')).toMatchObject({ status: 'draft', prompt: 'edited before run' })
    expect(result.nodes.find((item: TreeNode) => item.id === 'second')?.status).toBe('completed')
  })

  it('should continue independent branches after an error only when the workspace setting allows it', async () => {
    const tree = applyTreeCommand(workspace([node('root'), node('child', 'root'), node('other')]), {
      type: 'settings',
      settings: { ...getTreeSettings(workspace([])), continueOnError: true },
    })
    addMessage.mockResolvedValueOnce(response('attack-1', 'conversation-1', [
      message('system', 0, 'System prompt', 'attack-1'),
      message('user', 1, 'root', 'attack-1'),
      message('assistant', 2, 'blocked evidence', 'attack-1', 'blocked'),
    ])).mockResolvedValueOnce(response('attack-2', 'conversation-2', [
      message('system', 0, 'System prompt', 'attack-2'),
      message('user', 1, 'other', 'attack-2'),
      message('assistant', 2, 'independent reply', 'attack-2'),
    ]))
    const result = await runTree(tree, options(getRunNodeIds(tree)))
    expect(result.nodes.find((item: TreeNode) => item.id === 'root')?.status).toBe('error')
    expect(result.nodes.find((item: TreeNode) => item.id === 'child')?.status).toBe('draft')
    expect(result.nodes.find((item: TreeNode) => item.id === 'other')?.status).toBe('completed')
    expect(addMessage).toHaveBeenCalledTimes(2)
  })

  it('should surface unexpected on-node-completed failures without losing persisted responses', async () => {
    let resolveSecond: ((value: AddMessageResponse) => void) | undefined
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      if (request.pieces[0].original_value === 'second') {
        return new Promise<AddMessageResponse>((resolve) => { resolveSecond = resolve })
      }
      return response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', 1, request.pieces[0].original_value, attackId),
        message('assistant', 2, 'observed response', attackId),
      ])
    })
    const failure = runTree(configuredWorkspace([node('first'), node('second')], { concurrency: 2 }), {
      ...options(['first', 'second']),
      onNodeCompleted: async (_saved: TreeWorkspace, nodeId: string): Promise<void> => {
        if (nodeId === 'first') throw new Error('Unexpected scorer callback failure')
      },
    })
    await waitFor(() => expect(addMessage).toHaveBeenCalledTimes(2))
    resolveSecond?.(response('attack-2', 'conversation-2', [
      message('system', 0, 'System prompt', 'attack-2'),
      message('user', 1, 'second', 'attack-2'),
      message('assistant', 2, 'observed response', 'attack-2'),
    ]))
    await expect(failure).rejects.toThrow('Unexpected scorer callback failure')
    expect(snapshots[snapshots.length - 1].nodes.find((entry: TreeNode) => entry.id === 'first'))
      .toMatchObject({ status: 'completed' })
    expect(snapshots[snapshots.length - 1].nodes.find((entry: TreeNode) => entry.id === 'second'))
      .toMatchObject({ status: 'completed' })
  })

  it('should skip completion callbacks for preparation failures without assistant evidence and continue other four-way branches', async () => {
    let failedCreate = false
    createAttack.mockImplementation(async (request: CreateAttackRequest) => {
      if (!failedCreate) {
        failedCreate = true
        throw new Error('Create failed')
      }
      requests.push(request)
      return { attack_result_id: `attack-${requests.length}`, conversation_id: `conversation-${requests.length}`, created_at: TIME }
    })
    const onNodeCompleted = jest.fn(async (saved: TreeWorkspace, nodeId: string): Promise<void> => {
      const current = saved.nodes.find((entry: TreeNode) => entry.id === nodeId)
      if (!current?.messages?.some((entry: BackendMessage) => entry.role === 'assistant')) {
        throw new Error('No assistant evidence')
      }
    })
    const tree = configuredWorkspace([node('first'), node('second'), node('third'), node('fourth')], {
      concurrency: 4,
      continueOnError: true,
    })
    const result = await runTree(tree, {
      ...options(['first', 'second', 'third', 'fourth']),
      onNodeCompleted,
    })
    expect(result.nodes.filter((entry: TreeNode) => entry.status === 'completed')).toHaveLength(3)
    expect(result.nodes.filter((entry: TreeNode) => entry.status === 'error')).toHaveLength(1)
    expect(onNodeCompleted).toHaveBeenCalledTimes(3)
    expect(addMessage).toHaveBeenCalledTimes(3)
  })

  it('should retain received evidence on completion-save failure for save-only recovery', async () => {
    save.mockImplementationOnce(async (tree: TreeWorkspace) => {
      const saved = { ...tree, revision: tree.revision + 1 }; snapshots.push(saved); return saved
    }).mockImplementationOnce(async (tree: TreeWorkspace) => {
      const saved = { ...tree, revision: tree.revision + 1 }; snapshots.push(saved); return saved
    }).mockRejectedValueOnce(new Error('Quota exceeded'))
    const failure: unknown = await runTree(workspace([node('root'), node('child', 'root')]), options(['root', 'child']))
      .catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(TreePersistenceError)
    if (!(failure instanceof TreePersistenceError)) throw new Error('Expected recoverable evidence')
    expect(failure.workspace.nodes[0].status).toBe('completed')
    expect(failure.workspace.nodes[0].messages?.[1].message_pieces[0].converted_value).toBe('observed response')
    expect(failure.workspace.nodes[1].status).toBe('draft')
    expect(onUpdate.mock.calls[onUpdate.mock.calls.length - 1][0].nodes[0].status).toBe('running')
    const recovered = await save(failure.workspace)
    expect(recovered.nodes[0].status).toBe('completed')
    expect(addMessage).toHaveBeenCalledTimes(1)
  })

  it('should retain all four terminal responses after the first terminal save failure', async () => {
    let releaseFirst: ((value: AddMessageResponse) => void) | undefined
    let releaseSecond: ((value: AddMessageResponse) => void) | undefined
    let releaseThird: ((value: AddMessageResponse) => void) | undefined
    let releaseFourth: ((value: AddMessageResponse) => void) | undefined
    let terminalSaveFailed = false
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      const reply = response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', 1, request.pieces[0].original_value, attackId),
        message('assistant', 2, `${request.pieces[0].original_value}-reply`, attackId),
      ])
      if (request.pieces[0].original_value === 'first') return new Promise<AddMessageResponse>((resolve) => { releaseFirst = resolve })
      if (request.pieces[0].original_value === 'second') return new Promise<AddMessageResponse>((resolve) => { releaseSecond = resolve })
      if (request.pieces[0].original_value === 'third') return new Promise<AddMessageResponse>((resolve) => { releaseThird = resolve })
      if (request.pieces[0].original_value === 'fourth') return new Promise<AddMessageResponse>((resolve) => { releaseFourth = resolve })
      return reply
    })
    save.mockImplementation(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      const completed = tree.nodes.filter((entry: TreeNode) => entry.messages?.some((message) => message.role === 'assistant')).length
      if (completed === 1 && !terminalSaveFailed) {
        terminalSaveFailed = true
        throw new Error('Quota exceeded')
      }
      const saved = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(saved)
      return saved
    })
    const failure = runTree(configuredWorkspace([node('first'), node('second'), node('third'), node('fourth')], { concurrency: 4 }), {
      ...options(['first', 'second', 'third', 'fourth']),
      onPersistenceFailure: () => {
        releaseSecond?.(response('attack-2', 'conversation-2', [
          message('system', 0, 'System prompt', 'attack-2'),
          message('user', 1, 'second', 'attack-2'),
          message('assistant', 2, 'second-reply', 'attack-2'),
        ]))
        releaseThird?.(response('attack-3', 'conversation-3', [
          message('system', 0, 'System prompt', 'attack-3'),
          message('user', 1, 'third', 'attack-3'),
          message('assistant', 2, 'third-reply', 'attack-3'),
        ]))
        releaseFourth?.(response('attack-4', 'conversation-4', [
          message('system', 0, 'System prompt', 'attack-4'),
          message('user', 1, 'fourth', 'attack-4'),
          message('assistant', 2, 'fourth-reply', 'attack-4'),
        ]))
      },
    }).catch((error: unknown) => error)
    await waitFor(() => expect(addMessage).toHaveBeenCalledTimes(4))
    releaseFirst?.(response('attack-1', 'conversation-1', [
      message('system', 0, 'System prompt', 'attack-1'),
      message('user', 1, 'first', 'attack-1'),
      message('assistant', 2, 'first-reply', 'attack-1'),
    ]))
    const result = await failure
    expect(result).toBeInstanceOf(TreePersistenceError)
    if (!(result instanceof TreePersistenceError)) throw new Error('Expected persistence failure')
    expect(result.workspace.nodes.map((entry: TreeNode) => entry.status)).toEqual(['completed', 'completed', 'completed', 'completed'])
    expect(result.workspace.nodes.map((entry: TreeNode) => entry.messages?.[1].message_pieces[0].converted_value))
      .toEqual(['first-reply', 'second-reply', 'third-reply', 'fourth-reply'])
  })

  it('should keep breadth-first depth boundaries even when concurrency allows multiple roots', async () => {
    let releaseDepth: (() => void) | undefined
    const depthBarrier = new Promise<void>((resolve) => { releaseDepth = resolve })
    const promptsStarted: string[] = []
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      promptsStarted.push(request.pieces[0].original_value)
      const index = Number(attackId.split('-')[1]) - 1
      const turn = (requests[index].cutoff_index ?? 0) + 1
      return response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', turn, request.pieces[0].original_value, attackId),
        message('assistant', turn + 1, 'observed response', attackId),
      ])
    })
    const tree = configuredWorkspace(
      [node('left-root'), node('right-root'), node('left-child', 'left-root')],
      { concurrency: 2, traversal: 'breadth-first' },
    )
    const run = runTree(tree, {
      ...options(getRunNodeIds(tree)),
      onNodeCompleted: async (_saved: TreeWorkspace, nodeId: string): Promise<void> => {
        if (nodeId === 'right-root') await depthBarrier
      },
    })
    await waitFor(() => expect(promptsStarted).toEqual(['left-root', 'right-root']))
    expect(promptsStarted).not.toContain('left-child')
    releaseDepth?.()
    const result = await run
    expect(promptsStarted).toEqual(['left-root', 'right-root', 'left-child'])
    expect(result.nodes.map((entry: TreeNode) => entry.status)).toEqual(['completed', 'completed', 'completed'])
  })

  it('should stop dispatch after the first error while preserving other in-flight responses', async () => {
    let resolveOther: ((value: AddMessageResponse) => void) | undefined
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      if (request.pieces[0].original_value === 'first') {
        return response(attackId, request.target_conversation_id, [
          message('system', 0, 'System prompt', attackId),
          message('user', 1, 'first', attackId),
          message('assistant', 2, 'blocked', attackId, 'blocked'),
        ])
      }
      if (request.pieces[0].original_value === 'second') {
        return new Promise<AddMessageResponse>((resolve) => {
          resolveOther = resolve
        })
      }
      return response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', 1, request.pieces[0].original_value, attackId),
        message('assistant', 2, 'observed response', attackId),
      ])
    })
    const tree = configuredWorkspace([node('first'), node('second'), node('third')], { concurrency: 2 })
    const runningTree = runTree(tree, options(getRunNodeIds(tree)))
    await waitFor(() => expect(addMessage).toHaveBeenCalledTimes(2))
    resolveOther?.(response('attack-2', 'conversation-2', [
      message('system', 0, 'System prompt', 'attack-2'),
      message('user', 1, 'second', 'attack-2'),
      message('assistant', 2, 'observed response', 'attack-2'),
    ]))
    const result = await runningTree
    expect(result.nodes.find((entry: TreeNode) => entry.id === 'first')?.status).toBe('error')
    expect(result.nodes.find((entry: TreeNode) => entry.id === 'second')?.status).toBe('completed')
    expect(result.nodes.find((entry: TreeNode) => entry.id === 'third')?.status).toBe('draft')
    expect(addMessage).toHaveBeenCalledTimes(2)
  })

  it('should merge later in-flight evidence into the recovery candidate after a save failure', async () => {
    save.mockImplementation(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      const completed = tree.nodes.filter((entry: TreeNode) => entry.messages !== undefined).length
      if (completed === 1) throw new Error('Quota exceeded')
      const saved = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(saved)
      return saved
    })
    const tree = configuredWorkspace([node('first'), node('second')], { concurrency: 2 })
    const failure = await runTree(tree, options(getRunNodeIds(tree))).catch((error: unknown) => error)
    expect(failure).toBeInstanceOf(TreePersistenceError)
    if (!(failure instanceof TreePersistenceError)) throw new Error('Expected persistence failure')
    expect(failure.workspace.nodes.map((entry: TreeNode) => entry.status)).toEqual(['completed', 'completed'])
    expect(failure.workspace.nodes.every((entry: TreeNode) => entry.messages?.[1].message_pieces[0].converted_value === 'observed response')).toBe(true)
    expect(addMessage).toHaveBeenCalledTimes(2)
  })

  it('should merge score recovery with a newer durable edit and later four-way terminal evidence', async () => {
    let latest = parseTreeWorkspace(JSON.stringify(workspace([
      node('first'),
      node('second'),
      node('third'),
      node('fourth'),
      node('draft'),
    ])))
    let resolveFirst: ((value: AddMessageResponse) => void) | undefined
    let resolveSecond: ((value: AddMessageResponse) => void) | undefined
    let resolveThird: ((value: AddMessageResponse) => void) | undefined
    let resolveFourth: ((value: AddMessageResponse) => void) | undefined
    const saveLatest = jest.fn(async (tree: TreeWorkspace): Promise<TreeWorkspace> => {
      latest = parseTreeWorkspace(JSON.stringify({ ...tree, revision: tree.revision + 1 }))
      snapshots.push(latest)
      return latest
    })
    addMessage.mockImplementation(async (attackId: string, request: AddMessageRequest) => {
      if (request.pieces[0].original_value === 'first') {
        return new Promise<AddMessageResponse>((resolve) => { resolveFirst = resolve })
      }
      if (request.pieces[0].original_value === 'second') {
        return new Promise<AddMessageResponse>((resolve) => { resolveSecond = resolve })
      }
      if (request.pieces[0].original_value === 'third') {
        return new Promise<AddMessageResponse>((resolve) => { resolveThird = resolve })
      }
      if (request.pieces[0].original_value === 'fourth') {
        return new Promise<AddMessageResponse>((resolve) => { resolveFourth = resolve })
      }
      return response(attackId, request.target_conversation_id, [
        message('system', 0, 'System prompt', attackId),
        message('user', 1, request.pieces[0].original_value, attackId),
        message('assistant', 2, 'observed response', attackId),
      ])
    })
    const onNodeCompleted = jest.fn(async (saved: TreeWorkspace, nodeId: string): Promise<void> => {
      if (nodeId !== 'first') return
      throw new TreePersistenceError(withScore(saved, nodeId), new Error('Quota exceeded while saving score'))
    })
    const failure = runTree(configuredWorkspace([node('first'), node('second'), node('third'), node('fourth'), node('draft')], { concurrency: 4 }), {
      nodeIds: ['first', 'second', 'third', 'fourth'],
      save: saveLatest,
      onUpdate,
      getLatest: (): TreeWorkspace => latest,
      isStopped: (): boolean => false,
      onNodeCompleted,
      onPersistenceFailure: () => {
        latest = parseTreeWorkspace(JSON.stringify({
          ...latest,
          revision: latest.revision + 1,
          nodes: latest.nodes.map((entry: TreeNode) => entry.id === 'draft' ? { ...entry, prompt: 'edited draft' } : entry),
        }))
        resolveSecond?.(response('attack-2', 'conversation-2', [
          message('system', 0, 'System prompt', 'attack-2'),
          message('user', 1, 'second', 'attack-2'),
          message('assistant', 2, 'observed response', 'attack-2'),
        ]))
        resolveThird?.(response('attack-3', 'conversation-3', [
          message('system', 0, 'System prompt', 'attack-3'),
          message('user', 1, 'third', 'attack-3'),
          message('assistant', 2, 'observed response', 'attack-3'),
        ]))
        resolveFourth?.(response('attack-4', 'conversation-4', [
          message('system', 0, 'System prompt', 'attack-4'),
          message('user', 1, 'fourth', 'attack-4'),
          message('assistant', 2, 'observed response', 'attack-4'),
        ]))
      },
    }).catch((error: unknown) => error)
    await waitFor(() => expect(addMessage).toHaveBeenCalledTimes(4))
    resolveFirst?.(response('attack-1', 'conversation-1', [
      message('system', 0, 'System prompt', 'attack-1'),
      message('user', 1, 'first', 'attack-1'),
      message('assistant', 2, 'observed response', 'attack-1'),
    ]))
    const result = await failure
    expect(result).toBeInstanceOf(TreePersistenceError)
    if (!(result instanceof TreePersistenceError)) throw new Error('Expected score recovery failure')
    expect(onNodeCompleted).toHaveBeenCalledTimes(1)
    expect(addMessage).toHaveBeenCalledTimes(4)
    expect(result.workspace.revision).toBe(latest.revision)
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'first')?.scoreRuns).toEqual([scoreRun(result.workspace.nodes[0])])
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'second')).toMatchObject({ status: 'completed' })
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'third')).toMatchObject({ status: 'completed' })
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'fourth')).toMatchObject({ status: 'completed' })
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'draft')?.prompt).toBe('edited draft')
    expect(result.workspace.nodes.find((entry: TreeNode) => entry.id === 'second')?.messages?.[1].message_pieces[0].converted_value).toBe('observed response')
  })

  it('reports concurrency 1 for unsupported targets', async () => {
    const concurrency = jest.fn()
    getTarget.mockResolvedValue({
      ...TARGET,
      identifier: { ...TARGET.identifier, class_name: 'OtherTarget', class_module: 'pyrit.prompt_target.custom.other_target' },
    })
    const result = await runTree(configuredWorkspace([node('root'), node('other')], { concurrency: 2 }), {
      ...options(['root', 'other']),
      onConcurrencyResolved: concurrency,
    })
    expect(concurrency).toHaveBeenCalledWith(1)
    expect(result.nodes.map((entry: TreeNode) => entry.status)).toEqual(['completed', 'completed'])
  })

  it('surfaces concurrency discovery errors before any send', async () => {
    getTarget.mockRejectedValueOnce(new Error('Discovery failed'))
    await expect(runTree(configuredWorkspace([node('root')], { concurrency: 2 }), {
      ...options(['root']),
      onConcurrencyResolved: jest.fn(),
    })).rejects.toThrow('Discovery failed')
    expect(createAttack).not.toHaveBeenCalled()
    expect(addMessage).not.toHaveBeenCalled()
  })

  it.each(['missing', 'changed', 'system'])('should refuse %s cloned history before converter or target calls', async (kind: string) => {
    const initial = workspace([node('root'), { ...node('child', 'root'), converters: [{ type: 'Base64Converter', params: {} }] }])
    const completed = await runTree(initial, options(['root']))
    getMessages.mockImplementation(async (_attackId: string, conversationId: string) => ({
      conversation_id: conversationId,
      messages: kind === 'missing' ? [] : [
        message('system', 0, kind === 'system' ? 'different system' : CONFIGURATION.systemPrompt, 'copy'),
        message('user', 1, kind === 'changed' ? 'different prompt' : 'root', 'copy'),
        message('simulated_assistant', 2, 'observed response', 'copy'),
      ],
    }))
    const result = await runTree(completed, options(['child']))
    expect(result.nodes[1]).toMatchObject({ status: 'error', error: expect.stringContaining('required backend history') })
    expect(addMessage).toHaveBeenCalledTimes(1)
    expect(createConverter).not.toHaveBeenCalled()
  })

  it('should recover an interrupted turn using backend reads without sending or creating anything', async () => {
    const interrupted = workspace([{
      ...node('root'), status: 'running', attackResultId: 'attack-1', conversationId: 'conversation-1',
    }, node('child', 'root')])
    getMessages.mockResolvedValue({
      conversation_id: 'conversation-1',
      messages: [
        message('system', 0, CONFIGURATION.systemPrompt, 'recorded'),
        message('user', 1, 'root', 'recorded'),
        message('assistant', 2, 'Recorded reply', 'recorded'),
      ],
    })
    const recovered = await recoverTreeNode(interrupted, 'root', save)
    expect(recovered.nodes[0].status).toBe('completed')
    expect(getRunNodeIds(recovered)).toEqual(['child'])
    expect(createAttack).not.toHaveBeenCalled()
    expect(addMessage).not.toHaveBeenCalled()
  })

  it('should refuse recovery with no recorded response and retain unresolved state', async () => {
    const interrupted = workspace([{
      ...node('root'), status: 'running', attackResultId: 'attack-1', conversationId: 'conversation-1',
    }])
    getMessages.mockResolvedValue({
      conversation_id: 'conversation-1',
      messages: [message('system', 0, CONFIGURATION.systemPrompt, 'recorded'), message('user', 1, 'root', 'recorded')],
    })
    await expect(recoverTreeNode(interrupted, 'root', save)).rejects.toThrow('No completed response')
    expect(save).not.toHaveBeenCalled()
    expect(addMessage).not.toHaveBeenCalled()
  })
})
