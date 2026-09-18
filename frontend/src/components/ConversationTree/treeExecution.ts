import { attacksApi, convertersApi, targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  AddMessageResponse,
  BackendMessage,
  ConverterInstance,
  CreateAttackRequest,
  TargetInstance,
  TreeNode,
  TreeWorkspace,
} from '@/types'

import { getCurrentAttemptId, getNewRunNodeIds, getTreeLabels, getTreeSettings, isNodeHidden, parseTreeWorkspace } from './treeModel'

const INSPECT_HISTORY = 'Inspect backend history before retrying.'
const STOPPED_MESSAGE = `Stopped between requests; an in-flight request was not cancelled. ${INSPECT_HISTORY}`
const PIECE_FIELDS = [
  'id', 'original_value_data_type', 'converted_value_data_type', 'original_value',
  'original_value_url', 'original_value_mime_type', 'converted_value', 'converted_value_url',
  'converted_value_mime_type', 'original_filename', 'converted_filename',
  'prompt_metadata', 'response_error', 'response_error_description',
] as const
const SCORE_FIELDS = [
  'id', 'message_piece_id', 'scorer_type', 'score_type', 'score_value', 'status',
  'is_objective_score', 'score_category', 'score_rationale', 'timestamp',
] as const
const EXECUTION_UPDATE_KEYS = ['status', 'attackResultId', 'conversationId', 'lastSequence', 'messages', 'error'] as const
const RETAINED_UPDATE_KEYS = [...EXECUTION_UPDATE_KEYS, 'scoreRuns'] as const
const NODE_UPDATE_CONFLICT_MESSAGE = 'Attempt changed before execution update; stopped without overwriting edits.'
const PARALLEL_SAFE_TARGET_CLASSES = new Set([
  'pyrit.prompt_target.openai.openai_chat_target.OpenAIChatTarget',
  'pyrit.prompt_target.openai.openai_response_target.OpenAIResponseTarget',
])

/** Carries received evidence without representing it as a successful durable write. */
export class TreePersistenceError extends Error {
  readonly candidate: TreeWorkspace
  readonly workspace: TreeWorkspace

  constructor(candidate: TreeWorkspace, failure: unknown) {
    super(`Execution stopped because its state could not be saved: ${failure instanceof Error ? failure.message : 'Storage failed'}`)
    this.name = 'TreePersistenceError'
    Object.assign(this, { cause: failure })
    this.candidate = candidate
    this.workspace = candidate
  }
}

export interface RunOptions {
  readonly nodeIds: string[]
  readonly save: (workspace: TreeWorkspace) => Promise<TreeWorkspace>
  readonly onUpdate: (workspace: TreeWorkspace) => void
  readonly isStopped: () => boolean
  readonly getLatest?: () => TreeWorkspace
  readonly onNodeCompleted?: (workspace: TreeWorkspace, nodeId: string) => Promise<void>
  readonly commitNodeUpdate?: (nodeId: string, expected: TreeNode, update: Partial<TreeNode>) => Promise<TreeWorkspace>
  readonly onConcurrencyResolved?: (concurrency: number) => void
  readonly onPersistenceFailure?: (candidate: TreeWorkspace) => void
}

interface FrozenNodeSignature {
  readonly nodeId: string
  readonly signature: string
}

function requireCondition(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function findNode(workspace: TreeWorkspace, nodeId: string): TreeNode {
  const node = workspace.nodes.find((item: TreeNode) => item.id === nodeId)
  requireCondition(node, 'Run contains a missing node')
  return node
}

function isNodeUpdateConflict(failure: unknown): boolean {
  return failure instanceof Error && failure.message === NODE_UPDATE_CONFLICT_MESSAGE
}

function validateQueue(workspace: TreeWorkspace, nodeIds: string[]): void {
  const planned = getNewRunNodeIds(workspace, nodeIds)
  requireCondition(planned.length === nodeIds.length && planned.every((nodeId: string, index: number) => nodeId === nodeIds[index]),
    'Run must be parent-first, with each parent completed or included earlier in the queue')
}

function frozenSignature(node: TreeNode): string {
  return JSON.stringify({
    prompt: node.prompt,
    converters: node.converters,
    parentId: node.parentId,
    attemptId: getCurrentAttemptId(node),
  })
}

function mutableExecutionSignature(node: TreeNode): string {
  return JSON.stringify({
    prompt: node.prompt,
    converters: node.converters,
    parentId: node.parentId,
    attemptId: getCurrentAttemptId(node),
  })
}

function matchesFrozenNode(node: TreeNode, frozen: FrozenNodeSignature): boolean {
  return frozenSignature(node) === frozen.signature
}

function validateTarget(target: TargetInstance, workspace: TreeWorkspace): void {
  requireCondition(target.target_registry_name === workspace.targetRegistryName &&
    target.identifier?.hash === workspace.targetIdentifierHash,
  'Target alias identity changed. Create a workspace for the new target; this tree was not sent.')
  const capabilities = target.capabilities
  requireCondition(capabilities?.supports_multi_turn === true && capabilities.supports_editable_history === true &&
    Array.isArray(capabilities.supported_input_modalities) && capabilities.supported_input_modalities.includes('text'),
  'Conversation trees require a target with multi-turn, editable history, and text input support.')
  requireCondition(!workspace.systemPrompt || capabilities.supports_system_prompt === true,
    'The selected target does not support a system prompt.')
}

function validateConverter(instance: ConverterInstance, converterId: string, converterType: string): void {
  requireCondition(instance.converter_id === converterId && instance.identifier?.class_name === converterType,
    'Created converter identity does not match the requested pipeline.')
  const inputTypes = instance.identifier.supported_input_types
  const outputTypes = instance.identifier.supported_output_types
  requireCondition(Array.isArray(inputTypes) && inputTypes.includes('text') &&
    Array.isArray(outputTypes) && outputTypes.length > 0 &&
    outputTypes.every((dataType: string) => dataType === 'text'),
  'Conversation-tree converters must declare text input support and exclusively text output. Missing or mixed output capabilities are not accepted.')
}

function pick<T extends object, K extends keyof T>(value: T, fields: readonly K[]): Pick<T, K> {
  requireCondition(value !== null && typeof value === 'object' && !Array.isArray(value), 'Malformed backend evidence')
  const result = { ...value }
  for (const field of Object.keys(result)) {
    if (!fields.some((key: K) => key === field)) Reflect.deleteProperty(result, field)
  }
  return result
}

export function projectBackendMessages(messages: BackendMessage[], afterSequence: number): BackendMessage[] {
  requireCondition(Array.isArray(messages), 'Backend returned no message history')
  const candidates = messages.filter((message: BackendMessage) => message.turn_number > afterSequence)
  const current = candidates.filter((message: BackendMessage) => message.role !== 'system')
  requireCondition(current.length > 0, 'Backend returned no evidence for the current turn')
  return current.map((message: BackendMessage): BackendMessage => {
    requireCondition(Array.isArray(message.message_pieces), 'Malformed backend message pieces')
    const pieces = message.message_pieces.map((piece: BackendMessage['message_pieces'][number]) => {
      requireCondition(Array.isArray(piece.scores), 'Malformed backend scores')
      return {
        ...pick(piece, PIECE_FIELDS),
        scores: piece.scores.map((score: BackendMessage['message_pieces'][number]['scores'][number]) => pick(score, SCORE_FIELDS)),
      }
    })
    return { ...pick(message, ['turn_number', 'role', 'created_at']), message_pieces: pieces }
  })
}

function currentMessages(response: AddMessageResponse, afterSequence: number): BackendMessage[] {
  requireCondition(Array.isArray(response.messages?.messages), 'Backend returned no message history')
  return projectBackendMessages(response.messages.messages, afterSequence)
}

export function historySignature(messages: BackendMessage[]): string {
  return JSON.stringify(messages.map((message: BackendMessage) => ({
    sequence: message.turn_number,
    role: message.role === 'simulated_assistant' ? 'assistant' : message.role,
    pieces: message.message_pieces.map((piece: BackendMessage['message_pieces'][number]) => ({
      originalType: piece.original_value_data_type,
      convertedType: piece.converted_value_data_type,
      original: piece.original_value,
      converted: piece.converted_value,
      error: piece.response_error,
      metadata: piece.prompt_metadata ?? {},
    })),
  })))
}

function verifyHistory(workspace: TreeWorkspace, node: TreeNode, messages: BackendMessage[], prefixOnly = false): void {
  const ancestors: TreeNode[] = []
  let parentId = node.parentId
  while (parentId !== null) {
    const parent = findNode(workspace, parentId)
    ancestors.unshift(parent)
    parentId = parent.parentId
  }
  const expected = ancestors.flatMap((ancestor: TreeNode) => ancestor.messages ?? [])
  const systemOffset = workspace.systemPrompt ? 1 : 0
  if (workspace.systemPrompt) {
    const system = messages[0]
    requireCondition(system?.role === 'system' && system.message_pieces.length === 1 &&
      system.message_pieces[0].original_value_data_type === 'text' &&
      system.message_pieces[0].converted_value_data_type === 'text' &&
      system.message_pieces[0].original_value === workspace.systemPrompt &&
      system.message_pieces[0].converted_value === workspace.systemPrompt,
    'Backend conversation is missing the workspace system prompt.')
  }
  requireCondition(prefixOnly || messages.length === expected.length + systemOffset,
    'Backend conversation does not contain the expected parent history.')
  requireCondition(historySignature(messages.slice(systemOffset, systemOffset + expected.length)) === historySignature(expected),
    'Backend conversation history differs from the observed ancestors. No new prompt was sent.')
}

function refreshLatest(latest: TreeWorkspace, getLatest?: RunOptions['getLatest']): TreeWorkspace {
  if (!getLatest) return parseTreeWorkspace(JSON.stringify(latest))
  const current = parseTreeWorkspace(JSON.stringify(getLatest()))
  return current.revision > latest.revision ? current : parseTreeWorkspace(JSON.stringify(latest))
}

function buildNodeUpdateCandidate(workspace: TreeWorkspace, expected: TreeNode, update: Partial<TreeNode>): TreeWorkspace {
  const latest = parseTreeWorkspace(JSON.stringify(workspace))
  const current = findNode(latest, expected.id)
  requireCondition(mutableExecutionSignature(current) === mutableExecutionSignature(expected),
    NODE_UPDATE_CONFLICT_MESSAGE)
  const nextNode = { ...current }
  for (const key of EXECUTION_UPDATE_KEYS) {
    if (!(key in update)) continue
    const value = update[key]
    if (value === undefined) Reflect.deleteProperty(nextNode, key)
    else Object.assign(nextNode, { [key]: value })
  }
  return parseTreeWorkspace(JSON.stringify({
    ...latest,
    nodes: latest.nodes.map((node: TreeNode) => node.id === expected.id ? nextNode : node),
  }))
}

async function saveExecutionState(
  latest: TreeWorkspace,
  expected: TreeNode,
  update: Partial<TreeNode>,
  options: RunOptions,
): Promise<TreeWorkspace> {
  const base = refreshLatest(latest, options.getLatest)
  const candidate = buildNodeUpdateCandidate(base, expected, update)
  if (options.commitNodeUpdate) {
    try {
      return parseTreeWorkspace(JSON.stringify(await options.commitNodeUpdate(expected.id, expected, update)))
    } catch (failure) {
      if (isNodeUpdateConflict(failure)) throw failure
      throw new TreePersistenceError(candidate, failure)
    }
  }
  let saved: TreeWorkspace
  try {
    saved = await options.save(candidate)
  } catch (failure) {
    throw new TreePersistenceError(candidate, failure)
  }
  options.onUpdate(saved)
  return parseTreeWorkspace(JSON.stringify(saved))
}

async function notifyNodeCompleted(options: RunOptions, latest: TreeWorkspace, nodeId: string): Promise<TreeWorkspace> {
  if (!options.onNodeCompleted) return latest
  await options.onNodeCompleted(latest, nodeId)
  return refreshLatest(latest, options.getLatest)
}

function mergeRetainedWorkspace(base: TreeWorkspace, retained: TreeWorkspace): TreeWorkspace {
  const merged = parseTreeWorkspace(JSON.stringify(base))
  const retainedById = new Map(retained.nodes.map((node: TreeNode) => [node.id, node]))
  merged.nodes = merged.nodes.map((node: TreeNode) => {
    const overlay = retainedById.get(node.id)
    if (!overlay || mutableExecutionSignature(node) !== mutableExecutionSignature(overlay)) return node
    const next = { ...node }
    for (const key of RETAINED_UPDATE_KEYS) {
      if (!(key in overlay)) continue
      const value = overlay[key]
      if (value === undefined) Reflect.deleteProperty(next, key)
      else Object.assign(next, { [key]: value })
    }
    return next
  })
  return parseTreeWorkspace(JSON.stringify(merged))
}

function validateResponseIdentity(response: AddMessageResponse, node: TreeNode, workspace: TreeWorkspace): void {
  requireCondition(response.attack?.attack_result_id === node.attackResultId &&
    response.messages?.conversation_id === node.conversationId &&
    response.attack.conversation_id === node.conversationId,
  'Backend response identity does not match the node')
  requireCondition(response.attack.target?.identifier_hash === workspace.targetIdentifierHash,
    'Backend response target identity does not match this workspace')
}

function errorMessage(failure: unknown): string {
  const normalized = toApiError(failure)
  if (normalized.isNetworkError || normalized.isTimeout || normalized.status === null) {
    return `Request outcome may be unknown. ${INSPECT_HISTORY}`
  }
  return `Backend request failed (HTTP ${normalized.status}). ${INSPECT_HISTORY}`
}

function getFrozenNodes(workspace: TreeWorkspace, nodeIds: string[]): Map<string, FrozenNodeSignature> {
  return new Map(nodeIds.map((nodeId: string) => {
    const node = findNode(workspace, nodeId)
    return [nodeId, { nodeId, signature: frozenSignature(node) }]
  }))
}

function shouldDispatchNode(
  workspace: TreeWorkspace,
  frozen: FrozenNodeSignature,
): TreeNode | undefined {
  const current = workspace.nodes.find((node: TreeNode) => node.id === frozen.nodeId)
  if (!current || current.status !== 'draft' || isNodeHidden(workspace, current.id)) return undefined
  if (!matchesFrozenNode(current, frozen)) return undefined
  if (current.parentId === null) return current
  const parent = workspace.nodes.find((node: TreeNode) => node.id === current.parentId)
  if (!parent || parent.status !== 'completed' || current.parentAttemptId !== getCurrentAttemptId(parent)) return undefined
  return current
}

function findPreparedNode(workspace: TreeWorkspace, frozen: FrozenNodeSignature): TreeNode | undefined {
  const current = workspace.nodes.find((node: TreeNode) => node.id === frozen.nodeId)
  if (!current || current.status !== 'running' || isNodeHidden(workspace, current.id) || !matchesFrozenNode(current, frozen)) return undefined
  if (current.parentId === null) return current
  const parent = workspace.nodes.find((node: TreeNode) => node.id === current.parentId)
  if (!parent || parent.status !== 'completed' || current.parentAttemptId !== getCurrentAttemptId(parent)) return undefined
  return current
}

function relativeDepths(workspace: TreeWorkspace, nodeIds: string[]): Map<string, number> {
  const queued = new Set(nodeIds)
  const depths = new Map<string, number>()
  for (const nodeId of nodeIds) {
    let depth = 0
    let current = findNode(workspace, nodeId)
    while (current.parentId !== null && queued.has(current.parentId)) {
      depth += 1
      current = findNode(workspace, current.parentId)
    }
    depths.set(nodeId, depth)
  }
  return depths
}

async function resolveExecutionConcurrency(workspace: TreeWorkspace, requested: number): Promise<number> {
  if (requested <= 1) return 1
  const target = await targetsApi.getTarget(workspace.targetRegistryName)
  validateTarget(target, workspace)
  const qualifiedName = `${target.identifier.class_module}.${target.identifier.class_name}`
  return PARALLEL_SAFE_TARGET_CLASSES.has(qualifiedName) ? requested : 1
}

/** Reconcile an interrupted persisted turn from backend evidence; never creates or sends a prompt. */
export async function recoverTreeNode(
  workspace: TreeWorkspace,
  nodeId: string,
  save: RunOptions['save'],
): Promise<TreeWorkspace> {
  const snapshot = parseTreeWorkspace(JSON.stringify(workspace))
  const node = findNode(snapshot, nodeId)
  requireCondition(node.status === 'running' && node.attackResultId && node.conversationId,
    'Only interrupted turns with recorded backend identifiers can be recovered.')
  const attack = await attacksApi.getAttack(node.attackResultId)
  const messages = await attacksApi.getMessages(node.attackResultId, node.conversationId)
  const response = { attack, messages }
  validateResponseIdentity(response, node, snapshot)
  verifyHistory(snapshot, node, messages.messages, true)
  const parent = node.parentId === null ? null : findNode(snapshot, node.parentId)
  const evidence = currentMessages(response, parent?.lastSequence ?? -1)
  requireCondition(evidence.some((message: BackendMessage) => message.role === 'assistant'),
    'No completed response is recorded yet. Inspect backend history; no prompt was resent.')
  const hasError = evidence.some((message: BackendMessage) =>
    message.message_pieces.some((piece: BackendMessage['message_pieces'][number]) => piece.response_error !== 'none'))
  const candidate = parseTreeWorkspace(JSON.stringify({
    ...snapshot,
    nodes: snapshot.nodes.map((item: TreeNode) => item.id === nodeId ? {
      ...item,
      status: hasError ? 'error' : 'completed',
      messages: evidence,
      lastSequence: evidence[evidence.length - 1].turn_number,
      ...(hasError ? { error: `The backend recorded an error response. ${INSPECT_HISTORY}` } : { error: undefined }),
    } : item),
  }))
  try {
    return await save(candidate)
  } catch (failure) {
    throw new TreePersistenceError(candidate, failure)
  }
}

/**
 * Bounded, human-approved execution. State writes stay serialized even when
 * backend requests run in parallel, so external edits and save failures never
 * discard already observed evidence.
 */
export async function runTree(workspace: TreeWorkspace, options: RunOptions): Promise<TreeWorkspace> {
  const approved = parseTreeWorkspace(JSON.stringify(workspace))
  const frozenSettings = getTreeSettings(approved)
  const queue = [...options.nodeIds]
  validateQueue(approved, queue)
  const frozenNodes = getFrozenNodes(approved, queue)
  const queuedNodeIds = new Set(queue)
  const depths = relativeDepths(approved, queue)
  const concurrency = await resolveExecutionConcurrency(approved, frozenSettings.concurrency ?? 1)
  options.onConcurrencyResolved?.(concurrency)
  let latest = approved
  let stateTail: Promise<unknown> = Promise.resolve()
  let persistenceFailure: TreePersistenceError | undefined
  let persistenceCause: unknown
  let unexpectedFailure: unknown
  let dispatchStopped = options.isStopped()
  let persistenceReported = false

  type NodeState = 'pending' | 'inflight' | 'completed' | 'error' | 'skipped'
  type NodeOutcome = { readonly nodeId: string; readonly outcome: 'completed' | 'error' | 'skipped' | 'stopped' | 'persistence' }
  const states = new Map<string, NodeState>(queue.map((nodeId: string) => [nodeId, 'pending']))
  const inflight = new Map<string, Promise<NodeOutcome>>()

  function currentWorkspace(): TreeWorkspace {
    const base = refreshLatest(latest, options.getLatest)
    return persistenceFailure ? mergeRetainedWorkspace(base, latest) : base
  }

  function queueStateTask<T>(task: () => Promise<T>): Promise<T> {
    const queued = stateTail.then(task, task)
    stateTail = queued.then(() => undefined, () => undefined)
    return queued
  }

  function recordPersistenceFailure(failure: TreePersistenceError): void {
    persistenceFailure = failure
    persistenceCause = (failure as Error & { cause?: unknown }).cause ?? failure
    latest = failure.workspace
    dispatchStopped = true
    if (!persistenceReported) {
      persistenceReported = true
      options.onPersistenceFailure?.(failure.workspace)
    }
  }

  async function persist(expected: TreeNode, update: Partial<TreeNode>): Promise<TreeWorkspace> {
    return queueStateTask(async () => {
      if (persistenceFailure) {
        latest = buildNodeUpdateCandidate(currentWorkspace(), expected, update)
        return latest
      }
      try {
        latest = await saveExecutionState(latest, expected, update, options)
        return latest
      } catch (failure) {
        if (failure instanceof TreePersistenceError) recordPersistenceFailure(failure)
        throw failure
      }
    })
  }

  async function persistTerminal(expected: TreeNode, update: Partial<TreeNode>, notifyCompletion = false): Promise<TreeWorkspace> {
    await persist(expected, update)
    await queueStateTask(async () => {
      if (persistenceFailure || !notifyCompletion || !options.onNodeCompleted) return latest
      try {
        latest = await notifyNodeCompleted(options, latest, expected.id)
        return latest
      } catch (failure) {
        if (failure instanceof TreePersistenceError) recordPersistenceFailure(failure)
        throw failure
      }
    })
    return currentWorkspace()
  }

  async function stopBeforeSend(nodeId: string): Promise<boolean> {
    if (!options.isStopped() && !dispatchStopped && !persistenceFailure) return false
    const current = currentWorkspace().nodes.find((node: TreeNode) => node.id === nodeId)
    if (current?.status === 'running') await persistTerminal(current, { status: 'error', error: STOPPED_MESSAGE })
    return true
  }

  function dependencySatisfied(nodeId: string): boolean {
    const node = findNode(approved, nodeId)
    if (node.parentId === null || !queuedNodeIds.has(node.parentId)) return true
    return states.get(node.parentId) === 'completed'
  }

  function drainBlockedNodes(): void {
    for (const nodeId of queue) {
      if (states.get(nodeId) !== 'pending') continue
      const node = findNode(approved, nodeId)
      if (node.parentId === null || !queuedNodeIds.has(node.parentId)) continue
      const parentState = states.get(node.parentId)
      if (parentState === 'error' || parentState === 'skipped') states.set(nodeId, 'skipped')
    }
  }

  function nextReadyNodeIds(): string[] {
    drainBlockedNodes()
    const pending = queue.filter((nodeId: string) => states.get(nodeId) === 'pending' && dependencySatisfied(nodeId))
    if (pending.length === 0) return []
    if (frozenSettings.traversal === 'depth-first') return pending
    const activeDepth = Math.min(...queue
      .filter((nodeId: string) => {
        const state = states.get(nodeId)
        return state === 'pending' || state === 'inflight'
      })
      .map((nodeId: string) => depths.get(nodeId) ?? 0))
    return pending.filter((nodeId: string) => (depths.get(nodeId) ?? 0) === activeDepth)
  }

  function settleNodeState(nodeId: string, outcome: NodeOutcome['outcome']): void {
    if (outcome === 'completed') {
      states.set(nodeId, 'completed')
      return
    }
    if (outcome === 'skipped') {
      states.set(nodeId, 'skipped')
      return
    }
    const current = currentWorkspace().nodes.find((node: TreeNode) => node.id === nodeId)
    if (current?.status === 'completed') states.set(nodeId, 'completed')
    else if (current?.status === 'error') states.set(nodeId, 'error')
    else states.set(nodeId, 'skipped')
  }

  async function executeNode(nodeId: string, frozen: FrozenNodeSignature): Promise<NodeOutcome> {
    if (dispatchStopped || options.isStopped()) return { nodeId, outcome: 'skipped' }
    const queuedNode = shouldDispatchNode(currentWorkspace(), frozen)
    if (!queuedNode) return { nodeId, outcome: 'skipped' }
    try {
      await persist(queuedNode, { status: 'running', error: undefined })
    } catch (failure) {
      if (isNodeUpdateConflict(failure)) return { nodeId, outcome: 'skipped' }
      throw failure
    }
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    let target: TargetInstance
    try {
      target = await targetsApi.getTarget(currentWorkspace().targetRegistryName)
    } catch (failure: unknown) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), { status: 'error', error: errorMessage(failure) })
      return { nodeId, outcome: 'error' }
    }
    try {
      validateTarget(target, currentWorkspace())
    } catch (failure: unknown) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), {
        status: 'error',
        error: `${failure instanceof Error ? failure.message : 'Target validation failed.'} ${INSPECT_HISTORY}`,
      })
      return { nodeId, outcome: 'error' }
    }
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    const runningNode = findNode(currentWorkspace(), nodeId)
    const parent = runningNode.parentId === null ? null : findNode(currentWorkspace(), runningNode.parentId)
    const request: CreateAttackRequest = {
      target_registry_name: currentWorkspace().targetRegistryName,
      name: currentWorkspace().name,
      labels: getTreeLabels(currentWorkspace(), nodeId),
      ...(parent
        ? { source_conversation_id: parent.conversationId, cutoff_index: parent.lastSequence }
        : { system_prompt: currentWorkspace().systemPrompt }),
    }

    let created: Awaited<ReturnType<typeof attacksApi.createAttack>>
    try {
      created = await attacksApi.createAttack(request)
      requireCondition(created?.attack_result_id && created.conversation_id, 'Backend returned no attack or conversation ID')
    } catch (failure: unknown) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), { status: 'error', error: errorMessage(failure) })
      return { nodeId, outcome: 'error' }
    }
    await persist(findNode(currentWorkspace(), nodeId), {
      attackResultId: created.attack_result_id,
      conversationId: created.conversation_id,
    })
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    try {
      const attack = await attacksApi.getAttack(created.attack_result_id)
      requireCondition(attack.attack_result_id === created.attack_result_id &&
        attack.conversation_id === created.conversation_id &&
        attack.target?.identifier_hash === currentWorkspace().targetIdentifierHash,
      'New attack did not capture the approved target identity')
    } catch (failure: unknown) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), {
        status: 'error',
        error: `Unable to verify the created attack target. ${errorMessage(failure)}`,
      })
      return { nodeId, outcome: 'error' }
    }
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    try {
      const history = await attacksApi.getMessages(created.attack_result_id, created.conversation_id)
      requireCondition(history.conversation_id === created.conversation_id, 'Backend returned an unrelated conversation.')
      verifyHistory(currentWorkspace(), findNode(currentWorkspace(), nodeId), history.messages)
    } catch (failure) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), {
        status: 'error',
        error: `Unable to verify required backend history. ${failure instanceof Error ? failure.message : 'History unavailable.'} ${INSPECT_HISTORY}`,
      })
      return { nodeId, outcome: 'error' }
    }
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    const converterIds: string[] = []
    let converterFailure: string | undefined
    for (const converter of runningNode.converters) {
      let createdConverter: Awaited<ReturnType<typeof convertersApi.createConverter>>
      try {
        createdConverter = await convertersApi.createConverter(converter)
        requireCondition(typeof createdConverter.converter_id === 'string' && createdConverter.converter_id.length > 0,
          'Backend returned no converter ID')
      } catch (failure: unknown) {
        converterFailure = errorMessage(failure)
        break
      }
      if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }
      let instance: ConverterInstance
      try {
        instance = await convertersApi.getConverter(createdConverter.converter_id)
      } catch (failure: unknown) {
        converterFailure = `Unable to verify converter capabilities. ${errorMessage(failure)}`
        break
      }
      try {
        validateConverter(instance, createdConverter.converter_id, converter.type)
      } catch (failure: unknown) {
        converterFailure = `${failure instanceof Error ? failure.message : 'Converter validation failed.'} ${INSPECT_HISTORY}`
        break
      }
      if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }
      converterIds.push(createdConverter.converter_id)
    }
    if (converterFailure) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), { status: 'error', error: converterFailure })
      return { nodeId, outcome: 'error' }
    }
    if (await stopBeforeSend(nodeId)) return { nodeId, outcome: 'stopped' }

    const prepared = findPreparedNode(currentWorkspace(), frozen)
    if (!prepared) return { nodeId, outcome: 'skipped' }

    let response: AddMessageResponse
    try {
      requireCondition(prepared.attackResultId && prepared.conversationId, 'Backend did not create independent node IDs')
      response = await attacksApi.addMessage(prepared.attackResultId, {
        role: 'user',
        pieces: [{ data_type: 'text', original_value: prepared.prompt }],
        send: true,
        target_registry_name: currentWorkspace().targetRegistryName,
        target_conversation_id: prepared.conversationId,
        converter_ids: converterIds,
      })
    } catch (failure: unknown) {
      await persistTerminal(findNode(currentWorkspace(), nodeId), { status: 'error', error: errorMessage(failure) })
      return { nodeId, outcome: 'error' }
    }

    let evidence: BackendMessage[]
    try {
      validateResponseIdentity(response, prepared, currentWorkspace())
      evidence = currentMessages(response, parent?.lastSequence ?? -1)
      parseTreeWorkspace(JSON.stringify({
        ...currentWorkspace(),
        nodes: currentWorkspace().nodes.map((item: TreeNode) => item.id === nodeId ? {
          ...item,
          status: 'error',
          error: 'Validating observed response',
          messages: evidence,
          lastSequence: evidence[evidence.length - 1].turn_number,
        } : item),
      }))
    } catch {
      await persistTerminal(findNode(currentWorkspace(), nodeId), {
        status: 'error',
        error: `Backend returned inconsistent or malformed evidence. ${INSPECT_HISTORY}`,
      })
      return { nodeId, outcome: 'error' }
    }

    const lastSequence = evidence[evidence.length - 1].turn_number
    let completionError: string | undefined
    if (evidence.some((message: BackendMessage) =>
      message.message_pieces.some((piece: BackendMessage['message_pieces'][number]) => piece.response_error !== 'none'))) {
      completionError = `The backend recorded an error response for this turn. ${INSPECT_HISTORY}`
    } else {
      try {
        parseTreeWorkspace(JSON.stringify({
          ...currentWorkspace(),
          nodes: currentWorkspace().nodes.map((item: TreeNode) => item.id === nodeId
            ? { ...item, status: 'completed', messages: evidence, lastSequence } : item),
        }))
      } catch {
        completionError = `The backend did not return a complete matching user/assistant turn. ${INSPECT_HISTORY}`
      }
    }
    const hasAssistantEvidence = evidence.some((message: BackendMessage) => message.role === 'assistant')
    await persistTerminal(findNode(currentWorkspace(), nodeId), {
      status: completionError ? 'error' : 'completed',
      messages: evidence,
      lastSequence,
      error: completionError,
    }, hasAssistantEvidence)
    return { nodeId, outcome: completionError ? 'error' : 'completed' }
  }

  async function trackNode(nodeId: string, frozen: FrozenNodeSignature): Promise<NodeOutcome> {
    try {
      return await executeNode(nodeId, frozen)
    } catch (failure) {
      if (failure instanceof TreePersistenceError) return { nodeId, outcome: 'persistence' }
      if (unexpectedFailure === undefined) unexpectedFailure = failure
      dispatchStopped = true
      return { nodeId, outcome: 'error' }
    }
  }

  function dispatchAvailable(): void {
    if (dispatchStopped || options.isStopped() || persistenceFailure) {
      dispatchStopped = true
      return
    }
    for (const nodeId of nextReadyNodeIds()) {
      if (inflight.size >= concurrency) break
      const frozen = frozenNodes.get(nodeId)
      if (!frozen) {
        states.set(nodeId, 'skipped')
        continue
      }
      states.set(nodeId, 'inflight')
      const task = trackNode(nodeId, frozen).then((result: NodeOutcome) => {
        inflight.delete(nodeId)
        settleNodeState(nodeId, result.outcome)
        if ((result.outcome === 'error' || result.outcome === 'stopped' || result.outcome === 'persistence') && !frozenSettings.continueOnError) {
          dispatchStopped = true
        }
        if (result.outcome === 'persistence') dispatchStopped = true
        return result
      })
      inflight.set(nodeId, task)
    }
  }

  dispatchAvailable()
  while (inflight.size > 0) {
    await Promise.race(Array.from(inflight.values()))
    dispatchAvailable()
  }
  await stateTail
  if (persistenceFailure) throw new TreePersistenceError(currentWorkspace(), persistenceCause ?? persistenceFailure)
  if (unexpectedFailure !== undefined) throw unexpectedFailure
  return currentWorkspace()
}
