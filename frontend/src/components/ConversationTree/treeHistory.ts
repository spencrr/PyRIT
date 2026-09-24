import { attacksApi } from '@/services/api'
import type { BackendMessage, TreeContinuation, TreeNode, TreeWorkspace } from '@/types'

import { projectBackendMessages } from './treeExecution'
import { getCurrentAttemptId, parseTreeWorkspace } from './treeModel'

const IMPORTED_RESPONSE_ERROR = 'The backend recorded an error response for this turn. Inspect backend history before retrying.'

function requireCondition(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function findNode(workspace: TreeWorkspace, nodeId: string): TreeNode {
  const node = workspace.nodes.find((entry: TreeNode) => entry.id === nodeId)
  requireCondition(node !== undefined, 'History import target no longer exists.')
  return node
}

function exactHistorySignature(messages: BackendMessage[]): string {
  return JSON.stringify(messages.map((message: BackendMessage) => ({
    turn: message.turn_number,
    role: message.role,
    pieces: message.message_pieces.map((piece: BackendMessage['message_pieces'][number]) => ({
      id: piece.id,
      originalType: piece.original_value_data_type,
      convertedType: piece.converted_value_data_type,
      original: piece.original_value,
      converted: piece.converted_value,
      error: piece.response_error,
    })),
  })))
}

function normalizeAssistantRole(role: string): 'assistant' | 'user' {
  if (role === 'assistant' || role === 'user') return role
  if (role === 'simulated_assistant') return 'assistant'
  throw new Error(`Unsupported backend continuation role '${role}'.`)
}

function hashText(value: string): string {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return (hash >>> 0).toString(16).padStart(8, '0')
}

function sanitizeIdentifier(value: string): string {
  return value.replace(/[^a-zA-Z0-9_.:-]/g, '_')
}

function importedNodeId(messages: BackendMessage[]): string {
  const firstPieceId = messages[0]?.message_pieces[0]?.id ?? ''
  const lastMessage = messages[messages.length - 1]
  const lastPieceId = lastMessage?.message_pieces[lastMessage.message_pieces.length - 1]?.id ?? ''
  const readable = `import:${sanitizeIdentifier(firstPieceId)}:${sanitizeIdentifier(lastPieceId)}`
  if (readable.length <= 220) return readable
  return `import:${hashText(firstPieceId)}:${hashText(lastPieceId)}:${hashText(`${firstPieceId}|${lastPieceId}`)}`
}

function importedAttemptId(nodeId: string): string {
  const readable = `${nodeId}:observed`
  return readable.length <= 256 ? readable : `import-attempt:${hashText(nodeId)}`
}

function normalizeContinuationMessage(message: BackendMessage): BackendMessage {
  const role = normalizeAssistantRole(message.role)
  if (role === 'user') {
    requireCondition(message.message_pieces.length === 1, 'Only single-piece user continuation turns are supported.')
    const [piece] = message.message_pieces
    requireCondition(piece.original_value_data_type === 'text' && piece.converted_value_data_type === 'text' &&
      typeof piece.original_value === 'string',
    'Only text user continuation turns are supported.')
  }
  return role === message.role ? message : { ...message, role }
}

function splitContinuationTurns(messages: BackendMessage[]): { turns: BackendMessage[][]; pendingMessages: number } {
  const turns: BackendMessage[][] = []
  let currentTurn: BackendMessage[] = []
  let awaitingAssistant = false
  for (const rawMessage of messages) {
    const message = normalizeContinuationMessage(rawMessage)
    if (message.role === 'user') {
      if (currentTurn.length === 0) {
        currentTurn = [message]
        awaitingAssistant = true
        continue
      }
      requireCondition(!awaitingAssistant, 'Only a single trailing user message can remain pending.')
      turns.push(currentTurn)
      currentTurn = [message]
      awaitingAssistant = true
      continue
    }
    requireCondition(currentTurn.length > 0, 'Backend continuation contains an assistant message without a matching user turn.')
    currentTurn = [...currentTurn, message]
    awaitingAssistant = false
  }
  if (currentTurn.length === 0) return { turns, pendingMessages: 0 }
  if (awaitingAssistant) return { turns, pendingMessages: 1 }
  return { turns: [...turns, currentTurn], pendingMessages: 0 }
}

function importedNodeSignature(node: TreeNode): string {
  return JSON.stringify({
    id: node.id,
    parentId: node.parentId,
    parentAttemptId: node.parentAttemptId,
    attemptId: node.attemptId,
    prompt: node.prompt,
    converters: node.converters,
    status: node.status,
    attackResultId: node.attackResultId,
    conversationId: node.conversationId,
    lastSequence: node.lastSequence,
    messages: exactHistorySignature(node.messages ?? []),
    error: node.error,
    importedFromBackend: node.importedFromBackend === true,
  })
}

function buildImportedNode(source: TreeNode, parent: TreeNode, messages: BackendMessage[]): TreeNode {
  const promptPiece = messages[0]?.message_pieces[0]
  requireCondition(promptPiece !== undefined && typeof promptPiece.original_value === 'string',
    'Continuation user messages must preserve their original text prompt.')
  const hasError = messages.some((message: BackendMessage) =>
    message.message_pieces.some((piece: BackendMessage['message_pieces'][number]) => piece.response_error !== 'none'))
  return {
    id: importedNodeId(messages),
    parentId: parent.id,
    parentAttemptId: getCurrentAttemptId(parent),
    attemptId: importedAttemptId(importedNodeId(messages)),
    prompt: promptPiece.original_value,
    converters: [],
    status: hasError ? 'error' : 'completed',
    pruned: false,
    kept: false,
    attackResultId: source.attackResultId,
    conversationId: source.conversationId,
    lastSequence: messages[messages.length - 1]?.turn_number,
    messages,
    ...(hasError ? { error: IMPORTED_RESPONSE_ERROR } : {}),
    importedFromBackend: true,
  }
}

function validateExistingImportedNode(existing: TreeNode, expected: TreeNode): void {
  requireCondition(importedNodeSignature(existing) === importedNodeSignature(expected),
    'Saved imported history no longer matches the backend conversation. Reload before importing again.')
}

export async function discoverTreeContinuation(workspace: TreeWorkspace, nodeId: string): Promise<TreeContinuation> {
  const snapshot = parseTreeWorkspace(JSON.stringify(workspace))
  const source = findNode(snapshot, nodeId)
  requireCondition(source.attackResultId !== undefined && source.conversationId !== undefined &&
    source.messages !== undefined && source.lastSequence !== undefined,
  'Only observed nodes with saved backend history can import continuation.')

  const attack = await attacksApi.getAttack(source.attackResultId)
  requireCondition(attack.attack_result_id === source.attackResultId &&
    attack.conversation_id === source.conversationId &&
    attack.target?.identifier_hash === snapshot.targetIdentifierHash,
  'Target identity changed in backend history. Create a new workspace for the new target.')

  const history = await attacksApi.getMessages(source.attackResultId, source.conversationId)
  requireCondition(history.conversation_id === source.conversationId, 'Backend returned an unrelated conversation.')

  const parent = source.parentId === null ? null : findNode(snapshot, source.parentId)
  let observed: BackendMessage[]
  try {
    observed = projectBackendMessages(history.messages, parent?.lastSequence ?? -1)
  } catch {
    throw new Error('Saved node history no longer matches the backend conversation. Reload before importing continuation.')
  }
  requireCondition(observed.length >= source.messages.length,
    'Saved node history no longer matches the backend conversation. Reload before importing continuation.')
  requireCondition(exactHistorySignature(observed.slice(0, source.messages.length)) === exactHistorySignature(source.messages),
    'Saved node history no longer matches the backend conversation. Reload before importing continuation.')

  const tail = observed.slice(source.messages.length).map((message: BackendMessage) => normalizeContinuationMessage(message))
  const { turns, pendingMessages } = splitContinuationTurns(tail)
  const existing = new Map(snapshot.nodes.map((node: TreeNode) => [node.id, node]))
  const discovered: TreeNode[] = []
  let anchor = source
  for (const turn of turns) {
    requireCondition(turn[0]?.role === 'user', 'Imported continuation turns must start with a user message.')
    requireCondition(turn.some((message: BackendMessage) => message.role === 'assistant'),
      'Imported continuation turns must include a complete assistant response.')
    const imported = buildImportedNode(source, anchor, turn)
    const current = existing.get(imported.id)
    if (current) {
      validateExistingImportedNode(current, imported)
      anchor = current
      continue
    }
    discovered.push(imported)
    existing.set(imported.id, imported)
    anchor = imported
  }
  if (discovered.length > 0) {
    const candidate = parseTreeWorkspace(JSON.stringify({ ...snapshot, nodes: [...snapshot.nodes, ...discovered] }))
    const importedIds = new Set(discovered.map((node: TreeNode) => node.id))
    return {
      workspaceId: snapshot.id,
      nodeId,
      attemptId: getCurrentAttemptId(source),
      nodes: candidate.nodes.filter((node: TreeNode) => importedIds.has(node.id)),
      pendingMessages,
    }
  }
  return {
    workspaceId: snapshot.id,
    nodeId,
    attemptId: getCurrentAttemptId(source),
    nodes: [],
    pendingMessages,
  }
}
