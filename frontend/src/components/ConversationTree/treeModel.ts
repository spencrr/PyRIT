import type {
  BackendMessage,
  BackendScore,
  TreeAttempt,
  TreeCommand,
  TreeConverterSpec,
  TreeGroup,
  TreeNode,
  TreeScoreRun,
  TreeScorerSelection,
  TreeSettings,
  TreeWorkspace,
} from '@/types'

export const MAX_TREE_NODES = 300
export const MAX_FAN_OUT = 20
export const MAX_RUN_CALLS = 50
export const TREE_WORKSPACE_LABEL = 'pyrit_tree_id'
export const TREE_NODE_LABEL = 'pyrit_tree_node_id'
export const TREE_ATTEMPT_LABEL = 'pyrit_tree_attempt_id'

const MAX_JSON_LENGTH = 5_000_000
const MAX_TEXT_LENGTH = 100_000
const MAX_JSON_DEPTH = 30
const MAX_JSON_VALUES = 100_000
const MAX_IDENTIFIER_LENGTH = 256
const MAX_OPERATION_BUDGET = 100_000
const MIN_NODE_WIDTH = 220
const MAX_NODE_WIDTH = 900
const MIN_NODE_HEIGHT = 180
const MAX_NODE_HEIGHT = 1000
const RUN_PARENT_FIRST_MESSAGE = 'Run the parent first'
const IMPORTED_NODE_ERROR = 'Imported history must remain an exact backend continuation'
const SHARED_BACKEND_HISTORY_ERROR = 'shared backend IDs are reserved for contiguous imported continuations'
const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])
const CREDENTIAL_KEY = /^(?:.*(?:api[_-]?key|password|secret|credential|private[_-]?key)|authorization|access[_-]?token|refresh[_-]?token|bearer[_-]?token|token)$/i
const WORKSPACE_KEYS = [
  'schemaVersion', 'id', 'revision', 'name', 'targetRegistryName', 'targetIdentifierHash',
  'systemPrompt', 'labels', 'nodes', 'createdAt', 'updatedAt', 'settings', 'groups',
]
const NODE_KEYS = [
  'id', 'parentId', 'prompt', 'converters', 'status', 'pruned', 'kept', 'forkedFrom',
  'position', 'attackResultId', 'conversationId', 'lastSequence', 'messages', 'error',
  'attemptId', 'parentAttemptId', 'attempts', 'scoreRuns', 'size', 'importedFromBackend',
]
const ATTEMPT_KEYS = [
  'attemptId', 'parentAttemptId', 'prompt', 'converters', 'status', 'attackResultId',
  'conversationId', 'lastSequence', 'messages', 'error', 'scoreRuns',
]
const GROUP_KEYS = ['id', 'kind', 'nodeIds', 'collapsed', 'activeNodeId']
const SETTINGS_KEYS = [
  'traversal', 'operationBudget', 'confirmRuns', 'autoRun', 'continueOnError', 'markdown',
  'stackSamples', 'stackVariants', 'autoScore', 'objective', 'scorers', 'primaryScorerId',
  'concurrency', 'edgeStyle', 'nodeSize',
]
const SCORER_SELECTION_KEYS = ['scorer_id', 'scorer_type', 'identifier_hash', 'score_type', 'scope', 'highIsRisk']
const SCORE_RUN_KEYS = ['id', 'scorerId', 'scorerHash', 'status', 'scores', 'error']
const PIECE_KEYS = [
  'id', 'original_value_data_type', 'converted_value_data_type', 'original_value',
  'original_value_url', 'original_value_mime_type', 'converted_value', 'converted_value_url',
  'converted_value_mime_type', 'original_filename', 'converted_filename', 'prompt_metadata',
  'scores', 'response_error', 'response_error_description',
]
const SCORE_KEYS = [
  'id', 'message_piece_id', 'scorer_type', 'score_type', 'score_value', 'status',
  'is_objective_score', 'score_category', 'score_rationale', 'timestamp',
]

type Configuration = Pick<TreeWorkspace,
  'name' | 'targetRegistryName' | 'targetIdentifierHash' | 'systemPrompt' | 'labels'>

type TraversalMode = TreeSettings['traversal']

type ExecutionStatus = TreeNode['status'] | TreeAttempt['status']

interface AttemptLike extends Omit<TreeAttempt, 'status'> {
  readonly status: ExecutionStatus
}

export const DEFAULT_TREE_SETTINGS: TreeSettings = {
  traversal: 'breadth-first',
  operationBudget: MAX_RUN_CALLS,
  confirmRuns: true,
  autoRun: false,
  continueOnError: false,
  markdown: false,
  stackSamples: true,
  stackVariants: false,
  autoScore: false,
  objective: '',
  scorers: [],
  concurrency: 1,
  edgeStyle: 'bezier',
  nodeSize: 'standard',
}

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(`Invalid conversation tree: ${message}`)
}

function record(value: unknown, context: string): Record<string, unknown> {
  requireValue(value !== null && typeof value === 'object' && !Array.isArray(value), `${context} must be an object`)
  return value as Record<string, unknown>
}

function onlyKeys(value: Record<string, unknown>, keys: readonly string[], context: string): void {
  requireValue(Object.keys(value).every((key: string) => keys.includes(key)), `${context} has unsupported fields`)
}

function text(value: unknown, context: string, allowEmpty = false, maximum = MAX_TEXT_LENGTH): asserts value is string {
  requireValue(typeof value === 'string' && value.length <= maximum && (allowEmpty || value.trim().length > 0),
    `${context} must be ${allowEmpty ? 'a' : 'a nonempty'} string of at most ${maximum} characters`)
}

function identifier(value: unknown, context: string): asserts value is string {
  text(value, context, false, MAX_IDENTIFIER_LENGTH)
  requireValue(/^[a-zA-Z0-9][a-zA-Z0-9_.:-]*$/.test(value) && !UNSAFE_KEYS.has(value),
    `${context} is not a safe identifier`)
}

function sequence(value: unknown, context: string): asserts value is number {
  requireValue(Number.isSafeInteger(value) && (value as number) >= 0, `${context} must be a nonnegative safe integer`)
}

function timestamp(value: unknown, context: string): void {
  text(value, context, false, 64)
  requireValue(/^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)), `${context} is not a timestamp`)
}

function parseJson(json: string): unknown {
  requireValue(typeof json === 'string' && json.length <= MAX_JSON_LENGTH, `JSON exceeds ${MAX_JSON_LENGTH} characters`)
  let parsed: unknown
  try {
    parsed = JSON.parse(json)
  } catch {
    throw new Error('Invalid conversation tree: malformed JSON')
  }
  let count = 0
  function visit(value: unknown, depth: number): void {
    count += 1
    requireValue(depth <= MAX_JSON_DEPTH && count <= MAX_JSON_VALUES, 'JSON is too deeply nested or complex')
    if (typeof value === 'number') requireValue(Number.isFinite(value), 'numbers must be finite')
    if (typeof value === 'string') text(value, 'JSON text', true)
    if (Array.isArray(value)) {
      for (const child of value) visit(child, depth + 1)
    } else if (value !== null && typeof value === 'object') {
      for (const [key, child] of Object.entries(value)) {
        requireValue(!UNSAFE_KEYS.has(key), 'unsafe object key')
        visit(child, depth + 1)
      }
    }
  }
  visit(parsed, 0)
  return parsed
}

function rejectCredentials(value: unknown): void {
  if (Array.isArray(value)) {
    for (const child of value) rejectCredentials(child)
  } else if (value !== null && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      requireValue(!CREDENTIAL_KEY.test(key), 'credentials must not be stored in plans or workspace configuration')
      rejectCredentials(child)
    }
  }
}

function buildRunBudgetMessage(limit: number): string {
  return `run exceeds the ${limit}-call budget (sends, converters, and auto-scoring)`
}

function initialAttemptId(nodeId: string): string {
  return `${nodeId}:initial`
}

function isObservedStatus(status: ExecutionStatus): status is 'completed' | 'error' {
  return status === 'completed' || status === 'error'
}

function newId(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  return Array.from(bytes, (byte: number) => byte.toString(16).padStart(2, '0')).join('')
}

function newAttemptId(): string {
  return newId()
}

function cloneSettings(settings: TreeSettings): TreeSettings {
  return {
    ...settings,
    scorers: settings.scorers.map((scorer: TreeScorerSelection) => ({ ...scorer })),
    ...(settings.primaryScorerId ? { primaryScorerId: settings.primaryScorerId } : {}),
  }
}

function normalizeSettings(settings?: Partial<TreeSettings>): TreeSettings {
  const normalized: TreeSettings = {
    ...cloneSettings(DEFAULT_TREE_SETTINGS),
    ...settings,
    scorers: Array.isArray(settings?.scorers)
      ? settings.scorers.map((scorer: TreeScorerSelection) => ({ ...scorer }))
      : [],
  }
  const primary = typeof settings?.primaryScorerId === 'string' &&
    normalized.scorers.some((scorer: TreeScorerSelection) => scorer.scorer_id === settings.primaryScorerId)
    ? settings.primaryScorerId
    : normalized.scorers[0]?.scorer_id
  normalized.concurrency = settings?.concurrency === 2 || settings?.concurrency === 4 ? settings.concurrency : 1
  normalized.edgeStyle = settings?.edgeStyle === 'smoothstep' || settings?.edgeStyle === 'straight' ? settings.edgeStyle : 'bezier'
  normalized.nodeSize = settings?.nodeSize === 'compact' || settings?.nodeSize === 'expanded' ? settings.nodeSize : 'standard'
  if (primary) normalized.primaryScorerId = primary
  else Reflect.deleteProperty(normalized, 'primaryScorerId')
  return normalized
}

function normalizeSettingsValue(value: unknown): unknown {
  const settings = record(value, 'settings')
  return normalizeSettings(settings as Partial<TreeSettings>)
}

function validateTreeScorerSelection(value: unknown): asserts value is TreeScorerSelection {
  const scorer = record(value, 'tree scorer')
  onlyKeys(scorer, SCORER_SELECTION_KEYS, 'tree scorer')
  identifier(scorer.scorer_id, 'scorer ID')
  text(scorer.scorer_type, 'scorer type')
  text(scorer.identifier_hash, 'scorer hash')
  requireValue(['true_false', 'float_scale', 'unknown'].includes(String(scorer.score_type)), 'invalid scorer score type')
  requireValue(scorer.scope === 'response' || scorer.scope === 'conversation', 'invalid scorer scope')
  requireValue(typeof scorer.highIsRisk === 'boolean', 'invalid scorer risk direction')
}

function validateTreeSettings(value: unknown): asserts value is TreeSettings {
  const settings = record(value, 'settings')
  onlyKeys(settings, SETTINGS_KEYS, 'settings')
  requireValue(settings.traversal === 'breadth-first' || settings.traversal === 'depth-first', 'invalid traversal')
  requireValue(Number.isSafeInteger(settings.operationBudget) && (settings.operationBudget as number) >= 1 &&
    (settings.operationBudget as number) <= MAX_OPERATION_BUDGET, `operation budget must be 1-${MAX_OPERATION_BUDGET}`)
  for (const key of [
    'confirmRuns', 'autoRun', 'continueOnError', 'markdown', 'stackSamples', 'stackVariants', 'autoScore',
  ] as const) {
    requireValue(typeof settings[key] === 'boolean', `${key} must be boolean`)
  }
  requireValue(settings.concurrency === 1 || settings.concurrency === 2 || settings.concurrency === 4, 'invalid concurrency')
  requireValue(settings.edgeStyle === 'bezier' || settings.edgeStyle === 'smoothstep' || settings.edgeStyle === 'straight', 'invalid edge style')
  requireValue(settings.nodeSize === 'compact' || settings.nodeSize === 'standard' || settings.nodeSize === 'expanded', 'invalid node size')
  text(settings.objective, 'objective', true)
  requireValue(Array.isArray(settings.scorers), 'scorers must be an array')
  const scorerIds = new Set<string>()
  for (const scorer of settings.scorers) {
    validateTreeScorerSelection(scorer)
    requireValue(!scorerIds.has(scorer.scorer_id), 'duplicate scorer selections are not allowed')
    scorerIds.add(scorer.scorer_id)
  }
  if (settings.primaryScorerId !== undefined) {
    identifier(settings.primaryScorerId, 'primary scorer ID')
    requireValue(scorerIds.has(settings.primaryScorerId), 'primary scorer must reference a selected scorer')
  }
}

export function getTreeSettings(workspace: Pick<TreeWorkspace, 'settings'>): TreeSettings {
  const normalized = normalizeSettings(workspace.settings)
  validateTreeSettings(normalized)
  return cloneSettings(normalized)
}

function converters(value: unknown): asserts value is TreeConverterSpec[] {
  requireValue(Array.isArray(value) && value.length <= MAX_RUN_CALLS, 'invalid converter pipeline')
  for (const item of value) {
    const spec = record(item, 'converter')
    onlyKeys(spec, ['type', 'params'], 'converter')
    identifier(spec.type, 'converter type')
    record(spec.params, 'converter params')
    rejectCredentials(spec.params)
  }
}

function validateBackendScore(value: unknown, context: string): asserts value is BackendScore {
  const score = record(value, context)
  onlyKeys(score, SCORE_KEYS, context)
  identifier(score.id, `${context} ID`)
  identifier(score.message_piece_id, `${context} piece ID`)
  text(score.scorer_type, `${context} scorer type`)
  text(score.score_type, `${context} score type`)
  timestamp(score.timestamp, `${context} timestamp`)
  if (score.score_value !== undefined && score.score_value !== null) text(score.score_value, `${context} value`, true)
  if (score.status !== undefined && score.status !== null) text(score.status, `${context} status`, true)
  if (score.score_rationale !== undefined && score.score_rationale !== null) text(score.score_rationale, `${context} rationale`, true)
  if (score.is_objective_score !== undefined) requireValue(typeof score.is_objective_score === 'boolean', `${context} objective flag`)
  if (score.score_category !== undefined && score.score_category !== null) {
    requireValue(Array.isArray(score.score_category), `${context} categories must be an array`)
    for (const category of score.score_category) text(category, `${context} category`)
  }
}

function validateMessages(value: unknown): asserts value is BackendMessage[] {
  requireValue(Array.isArray(value) && value.length > 0 && value.length <= MAX_TREE_NODES * 2, 'invalid message evidence')
  const pieceIds = new Set<string>()
  let previousSequence = -1
  for (const item of value) {
    const message = record(item, 'message')
    onlyKeys(message, ['turn_number', 'role', 'message_pieces', 'created_at'], 'message')
    sequence(message.turn_number, 'message sequence')
    requireValue(message.turn_number > previousSequence, 'message sequences must increase')
    previousSequence = message.turn_number
    text(message.role, 'message role', false, 64)
    timestamp(message.created_at, 'message timestamp')
    requireValue(Array.isArray(message.message_pieces) && message.message_pieces.length > 0, 'messages need pieces')
    const scoreIds = new Set<string>()
    for (const itemPiece of message.message_pieces) {
      const piece = record(itemPiece, 'message piece')
      onlyKeys(piece, PIECE_KEYS, 'message piece')
      identifier(piece.id, 'piece ID')
      requireValue(!pieceIds.has(piece.id), 'duplicate evidence piece IDs')
      pieceIds.add(piece.id)
      text(piece.original_value_data_type, 'original data type')
      text(piece.converted_value_data_type, 'converted data type')
      text(piece.converted_value, 'converted value', true)
      text(piece.response_error, 'response error')
      for (const key of PIECE_KEYS.filter((name: string) => ![
        'id', 'original_value_data_type', 'converted_value_data_type', 'converted_value',
        'response_error', 'scores', 'prompt_metadata',
      ].includes(name))) {
        if (piece[key] !== undefined && piece[key] !== null) text(piece[key], key, true)
      }
      if (piece.prompt_metadata !== undefined && piece.prompt_metadata !== null) {
        record(piece.prompt_metadata, 'prompt metadata')
        rejectCredentials(piece.prompt_metadata)
      }
      requireValue(Array.isArray(piece.scores), 'piece scores must be an array')
      for (const itemScore of piece.scores) {
        validateBackendScore(itemScore, 'score')
        const score = itemScore as BackendScore
        requireValue(score.message_piece_id === piece.id, 'score evidence does not match its piece')
        requireValue(!scoreIds.has(score.id), 'duplicate score IDs')
        scoreIds.add(score.id)
      }
    }
  }
}

function evidencePieceIds(messages: BackendMessage[] | undefined): Set<string> {
  const ids = new Set<string>()
  for (const message of messages ?? []) {
    for (const piece of message.message_pieces) ids.add(piece.id)
  }
  return ids
}

function validateTreeScoreRun(value: unknown, pieceIds?: Set<string>): asserts value is TreeScoreRun {
  const run = record(value, 'score run')
  onlyKeys(run, SCORE_RUN_KEYS, 'score run')
  identifier(run.id, 'score run ID')
  identifier(run.scorerId, 'score run scorer ID')
  text(run.scorerHash, 'score run scorer hash')
  requireValue(['complete', 'not_applicable', 'error'].includes(String(run.status)), 'invalid score run status')
  if (run.error !== undefined) {
    text(run.error, 'score run error')
    requireValue(run.status === 'error', 'only error score runs can include an error message')
  }
  requireValue(Array.isArray(run.scores), 'score run scores must be an array')
  const scoreIds = new Set<string>()
  for (const score of run.scores) {
    validateBackendScore(score, 'score run score')
    const typed = score as BackendScore
    if (pieceIds) requireValue(pieceIds.has(typed.message_piece_id), 'score run references an unknown message piece')
    requireValue(!scoreIds.has(typed.id), 'duplicate score IDs')
    scoreIds.add(typed.id)
  }
}

function validateTreeScoreRuns(value: unknown, pieceIds?: Set<string>): asserts value is TreeScoreRun[] {
  requireValue(Array.isArray(value), 'score runs must be an array')
  const ids = new Set<string>()
  for (const run of value) {
    validateTreeScoreRun(run, pieceIds)
    const typed = run as TreeScoreRun
    requireValue(!ids.has(typed.id), 'duplicate score run IDs')
    ids.add(typed.id)
  }
}

function validateAttemptState(value: AttemptLike, context: string): void {
  identifier(value.attemptId, `${context} attempt ID`)
  if (value.parentAttemptId !== undefined) identifier(value.parentAttemptId, `${context} parent attempt ID`)
  text(value.prompt, `${context} prompt`)
  converters(value.converters)
  if (value.attackResultId !== undefined) identifier(value.attackResultId, `${context} attack ID`)
  if (value.conversationId !== undefined) identifier(value.conversationId, `${context} conversation ID`)
  requireValue((value.attackResultId === undefined) === (value.conversationId === undefined),
    `${context} attack and conversation IDs must be paired`)
  if (value.lastSequence !== undefined) sequence(value.lastSequence, `${context} last sequence`)
  if (value.error !== undefined) text(value.error, `${context} error`)
  if (value.messages !== undefined) {
    validateMessages(value.messages)
    requireValue(value.attackResultId !== undefined, `${context} message evidence requires backend IDs`)
    requireValue(value.lastSequence === value.messages[value.messages.length - 1].turn_number,
      `${context} last sequence must match observed evidence`)
  } else {
    requireValue(value.lastSequence === undefined, `${context} last sequence requires observed messages`)
  }
  if (value.scoreRuns !== undefined) validateTreeScoreRuns(value.scoreRuns, evidencePieceIds(value.messages))
  if (value.status === 'draft') {
    requireValue(value.attackResultId === undefined && value.messages === undefined && value.error === undefined &&
      value.scoreRuns === undefined, `${context} drafts cannot contain execution evidence`)
  }
  if (value.status === 'running') {
    requireValue(value.messages === undefined && value.error === undefined && value.scoreRuns === undefined,
      `${context} running attempts cannot claim a response`)
  }
  if (value.status === 'error') {
    text(value.error, `${context} error status reason`)
    if (value.scoreRuns !== undefined) requireValue(value.messages !== undefined, `${context} score runs require current evidence`)
  }
  if (value.status === 'completed') {
    requireValue(value.error === undefined && value.messages !== undefined,
      `${context} completed evidence requires a response without an error`)
    const messages = value.messages
    const first = messages[0]
    const last = messages[messages.length - 1]
    requireValue(first.role === 'user' && last.role === 'assistant' && messages.length >= 2,
      `${context} evidence must contain the current user turn and assistant response`)
    requireValue(messages.filter((message: BackendMessage) => message.role === 'user').length === 1,
      `${context} evidence must contain only one user turn`)
    requireValue(first.message_pieces.length === 1 && first.message_pieces.every((piece: BackendMessage['message_pieces'][number]) =>
      piece.original_value_data_type === 'text' && piece.original_value === value.prompt), `${context} observed user prompt does not match`)
    requireValue(messages.every((message: BackendMessage) =>
      message.message_pieces.every((piece: BackendMessage['message_pieces'][number]) => piece.response_error === 'none')),
    `${context} completed evidence cannot contain error pieces`)
  }
}

function validateTreeAttempt(value: unknown): asserts value is TreeAttempt {
  const attempt = record(value, 'attempt')
  onlyKeys(attempt, ATTEMPT_KEYS, 'attempt')
  requireValue(attempt.status === 'completed' || attempt.status === 'error', 'invalid attempt status')
  validateAttemptState(attempt as unknown as TreeAttempt, 'attempt')
}

function validateNode(value: unknown): asserts value is TreeNode {
  const node = record(value, 'node')
  onlyKeys(node, NODE_KEYS, 'node')
  identifier(node.id, 'node ID')
  if (node.parentId !== null) identifier(node.parentId, 'parent ID')
  if (node.parentAttemptId !== undefined) identifier(node.parentAttemptId, 'parent attempt ID')
  text(node.prompt, 'prompt')
  converters(node.converters)
  requireValue(['draft', 'running', 'completed', 'error'].includes(String(node.status)), 'invalid node status')
  requireValue(typeof node.pruned === 'boolean' && typeof node.kept === 'boolean', 'invalid node flags')
  if (node.forkedFrom !== undefined) identifier(node.forkedFrom, 'fork source')
  if (node.position !== undefined) validateNodePosition(node.position, 'position')
  if (node.size !== undefined) validateNodeSize(node.size, 'node size')
  if (node.importedFromBackend !== undefined) requireValue(typeof node.importedFromBackend === 'boolean', 'invalid import flag')
  validateAttemptState({
    ...node,
    attemptId: node.attemptId === undefined ? initialAttemptId(node.id) : node.attemptId,
  } as unknown as AttemptLike, 'node')
  if (node.attempts !== undefined) {
    requireValue(Array.isArray(node.attempts), 'attempt history must be an array')
    for (const attempt of node.attempts) validateTreeAttempt(attempt)
  }
}

function validateNodeSize(value: unknown, context: string): asserts value is { width: number; height: number } {
  const size = record(value, context)
  onlyKeys(size, ['width', 'height'], context)
  requireValue(typeof size.width === 'number' && Number.isFinite(size.width) &&
    size.width >= MIN_NODE_WIDTH && size.width <= MAX_NODE_WIDTH,
  `${context} width must be ${MIN_NODE_WIDTH}-${MAX_NODE_WIDTH}`)
  requireValue(typeof size.height === 'number' && Number.isFinite(size.height) &&
    size.height >= MIN_NODE_HEIGHT && size.height <= MAX_NODE_HEIGHT,
  `${context} height must be ${MIN_NODE_HEIGHT}-${MAX_NODE_HEIGHT}`)
}

function validateNodePosition(value: unknown, context: string): asserts value is { x: number; y: number } {
  const position = record(value, context)
  onlyKeys(position, ['x', 'y'], context)
  requireValue(typeof position.x === 'number' && Number.isFinite(position.x) &&
    typeof position.y === 'number' && Number.isFinite(position.y), `${context} must have finite coordinates`)
}

function validateTreeGroup(value: unknown): asserts value is TreeGroup {
  const group = record(value, 'group')
  onlyKeys(group, GROUP_KEYS, 'group')
  identifier(group.id, 'group ID')
  requireValue(group.kind === 'sample' || group.kind === 'variant', 'invalid group kind')
  requireValue(Array.isArray(group.nodeIds) && group.nodeIds.length > 0, 'group must contain at least one node')
  const nodeIds = new Set<string>()
  for (const nodeId of group.nodeIds) {
    identifier(nodeId, 'group node ID')
    requireValue(!nodeIds.has(nodeId), 'group node IDs must be unique')
    nodeIds.add(nodeId)
  }
  requireValue(typeof group.collapsed === 'boolean', 'group collapsed must be boolean')
  identifier(group.activeNodeId, 'group active node ID')
  requireValue(nodeIds.has(group.activeNodeId), 'group active node must be a member')
}

function getNode(workspace: TreeWorkspace, nodeId: string): TreeNode {
  const node = workspace.nodes.find((item: TreeNode) => item.id === nodeId)
  requireValue(node, 'node not found')
  return node
}

function childrenByParent(workspace: TreeWorkspace): Map<string | null, TreeNode[]> {
  const children = new Map<string | null, TreeNode[]>()
  for (const node of workspace.nodes) {
    const list = children.get(node.parentId) ?? []
    list.push(node)
    children.set(node.parentId, list)
  }
  return children
}

function orderedSubtree(workspace: TreeWorkspace, rootId: string, includeHidden = true): TreeNode[] {
  const children = childrenByParent(workspace)
  const root = getNode(workspace, rootId)
  const result: TreeNode[] = []
  const queue: TreeNode[] = [root]
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) continue
    if (includeHidden || !isNodeHidden(workspace, current.id)) result.push(current)
    for (const child of children.get(current.id) ?? []) queue.push(child)
  }
  return result
}

function isDescendant(workspace: TreeWorkspace, node: TreeNode, rootId: string): boolean {
  let current = node
  while (current.id !== rootId && current.parentId !== null) current = getNode(workspace, current.parentId)
  return current.id === rootId
}

function hasRunningDescendant(workspace: TreeWorkspace, rootId: string): boolean {
  return orderedSubtree(workspace, rootId, true).some((node: TreeNode) => node.status === 'running')
}

function assertNoRunningSubtree(workspace: TreeWorkspace, rootId: string, action: string): void {
  requireValue(!hasRunningDescendant(workspace, rootId), `${action} is blocked until running descendants finish`)
}

export function getCurrentAttemptId(node: Pick<TreeNode, 'id' | 'attemptId'>): string {
  return node.attemptId ?? initialAttemptId(node.id)
}

function normalizeLegacyNode(value: unknown): Record<string, unknown> {
  const node = record(value, 'node')
  const normalized: Record<string, unknown> = {
    ...node,
    attemptId: node.attemptId === undefined && typeof node.id === 'string' ? initialAttemptId(node.id) : node.attemptId,
  }
  if (node.attempts === undefined) Reflect.deleteProperty(normalized, 'attempts')
  if (node.scoreRuns === undefined) Reflect.deleteProperty(normalized, 'scoreRuns')
  return normalized
}

function normalizeLegacyWorkspace(value: unknown): unknown {
  const workspace = record(value, 'workspace')
  const nodes = Array.isArray(workspace.nodes) ? workspace.nodes.map((node: unknown) => normalizeLegacyNode(node)) : workspace.nodes
  const normalized: Record<string, unknown> = { ...workspace, nodes }
  if (workspace.settings !== undefined) normalized.settings = normalizeSettingsValue(workspace.settings)
  const byId = new Map<string, Record<string, unknown>>()
  if (Array.isArray(nodes)) {
    for (const node of nodes) if (typeof node.id === 'string') byId.set(node.id, node)
    for (const node of nodes) {
      if (node.parentId !== null && node.parentAttemptId === undefined) {
        const parent = typeof node.parentId === 'string' ? byId.get(node.parentId) : undefined
        if (parent && typeof parent.id === 'string') {
          node.parentAttemptId = getCurrentAttemptId({
            id: parent.id,
            attemptId: typeof parent.attemptId === 'string' ? parent.attemptId : undefined,
          })
        }
      }
    }
  }
  return normalized
}

function validateGraph(nodes: TreeNode[]): void {
  requireValue(nodes.length <= MAX_TREE_NODES, `maximum ${MAX_TREE_NODES} nodes`)
  const byId = new Map<string, TreeNode>()
  const attempts = new Set<string>()
  const evidencePieces = new Set<string>()
  for (const node of nodes) {
    requireValue(!byId.has(node.id), 'duplicate node IDs')
    byId.set(node.id, node)
    const currentAttemptId = getCurrentAttemptId(node)
    requireValue(!attempts.has(currentAttemptId), 'duplicate current attempt IDs')
    attempts.add(currentAttemptId)
    for (const attempt of node.attempts ?? []) {
      requireValue(!attempts.has(attempt.attemptId), 'duplicate attempt IDs')
      attempts.add(attempt.attemptId)
    }
    for (const message of getTreeAttempts(node).flatMap((attempt: TreeAttempt) => attempt.messages ?? [])) {
      for (const piece of message.message_pieces) {
        requireValue(!evidencePieces.has(piece.id), 'evidence pieces cannot belong to multiple nodes')
        evidencePieces.add(piece.id)
      }
    }
  }
  for (const node of nodes) {
    const seen = new Set([node.id])
    let current = node
    while (current.parentId !== null) {
      const parent = byId.get(current.parentId)
      requireValue(parent, 'missing parent')
      requireValue(!seen.has(parent.id), 'cycle detected')
      seen.add(parent.id)
      current = parent
    }
    if (node.parentId === null) {
      requireValue(node.parentAttemptId === undefined, 'root nodes cannot have a parent attempt')
      continue
    }
    const parent = byId.get(node.parentId)
    if (!parent) continue
    if (node.status !== 'draft') {
      requireValue(parent.status === 'completed', 'observed or running children require a completed parent')
      requireValue(node.parentAttemptId === getCurrentAttemptId(parent), 'current child attempt must reference the parent current attempt')
      if (node.messages) requireValue(node.messages[0].turn_number > (parent.lastSequence ?? -1), 'child evidence overlaps parent history')
    }
  }
  validateSharedBackendHistory(nodes)
}

function validateGroups(workspace: TreeWorkspace): void {
  if (workspace.groups === undefined) return
  requireValue(Array.isArray(workspace.groups), 'groups must be an array')
  const byId = new Map(workspace.nodes.map((node: TreeNode) => [node.id, node]))
  const groupIds = new Set<string>()
  const memberships = new Map<string, string>()
  for (const group of workspace.groups) {
    validateTreeGroup(group)
    requireValue(!groupIds.has(group.id), 'duplicate group IDs')
    groupIds.add(group.id)
    const parentId = byId.get(group.nodeIds[0])?.parentId
    requireValue(parentId !== undefined || byId.has(group.nodeIds[0]), 'group references a missing node')
    for (const nodeId of group.nodeIds) {
      const node = byId.get(nodeId)
      requireValue(node, 'group references a missing node')
      requireValue(node.parentId === parentId, 'grouped nodes must share the same parent')
      const owner = memberships.get(nodeId)
      requireValue(owner === undefined || owner === group.id, 'groups cannot overlap')
      memberships.set(nodeId, group.id)
    }
  }
}

interface BackendConversationRecord {
  readonly attackResultId: string
  readonly conversationId: string
  readonly nodeId: string
  readonly parentId: string | null
  readonly importedFromBackend: boolean
}

function validateSharedBackendHistory(nodes: TreeNode[]): void {
  const grouped = new Map<string, BackendConversationRecord[]>()
  const attackPairs = new Map<string, string>()
  const conversationPairs = new Map<string, string>()
  for (const node of nodes) {
    const inventory = [...(node.attempts ?? []), currentAttemptState(node)]
    for (const attempt of inventory) {
      if (!attempt.attackResultId || !attempt.conversationId) continue
      const key = `${attempt.attackResultId}\u0000${attempt.conversationId}`
      const attackPair = attackPairs.get(attempt.attackResultId)
      const conversationPair = conversationPairs.get(attempt.conversationId)
      requireValue(attackPair === undefined || attackPair === key, SHARED_BACKEND_HISTORY_ERROR)
      requireValue(conversationPair === undefined || conversationPair === key, SHARED_BACKEND_HISTORY_ERROR)
      attackPairs.set(attempt.attackResultId, key)
      conversationPairs.set(attempt.conversationId, key)
      grouped.set(key, [...(grouped.get(key) ?? []), {
        attackResultId: attempt.attackResultId,
        conversationId: attempt.conversationId,
        nodeId: node.id,
        parentId: node.parentId,
        importedFromBackend: node.importedFromBackend === true,
      }])
    }
  }
  for (const records of grouped.values()) {
    if (records.length <= 1) continue
    const byNodeId = new Map<string, BackendConversationRecord>()
    const childCounts = new Map<string, number>()
    let roots = 0
    for (const record of records) {
      requireValue(!byNodeId.has(record.nodeId), SHARED_BACKEND_HISTORY_ERROR)
      byNodeId.set(record.nodeId, record)
    }
    for (const record of records) {
      const parent = record.parentId === null ? undefined : byNodeId.get(record.parentId)
      if (!parent) {
        roots += 1
        continue
      }
      requireValue(record.importedFromBackend, SHARED_BACKEND_HISTORY_ERROR)
      childCounts.set(parent.nodeId, (childCounts.get(parent.nodeId) ?? 0) + 1)
      requireValue((childCounts.get(parent.nodeId) ?? 0) <= 1, SHARED_BACKEND_HISTORY_ERROR)
    }
    requireValue(roots === 1, SHARED_BACKEND_HISTORY_ERROR)
  }
}

function validateWorkspace(value: unknown): asserts value is TreeWorkspace {
  const parsed = record(value, 'workspace')
  onlyKeys(parsed, WORKSPACE_KEYS, 'workspace')
  requireValue(parsed.schemaVersion === 1, 'unsupported schema version')
  identifier(parsed.id, 'workspace ID')
  sequence(parsed.revision, 'revision')
  text(parsed.name, 'workspace name', false, MAX_IDENTIFIER_LENGTH)
  text(parsed.targetRegistryName, 'target alias', false, MAX_IDENTIFIER_LENGTH)
  text(parsed.targetIdentifierHash, 'target identity hash', false, MAX_IDENTIFIER_LENGTH)
  text(parsed.systemPrompt, 'system prompt', true)
  const labels = record(parsed.labels, 'labels')
  rejectCredentials(labels)
  for (const [key, value] of Object.entries(labels)) {
    text(key, 'label key', false, MAX_IDENTIFIER_LENGTH)
    text(value, 'label value', true)
  }
  timestamp(parsed.createdAt, 'creation time')
  timestamp(parsed.updatedAt, 'update time')
  if (parsed.settings !== undefined) validateTreeSettings(parsed.settings)
  if (parsed.groups !== undefined) {
    requireValue(Array.isArray(parsed.groups), 'groups must be an array')
    for (const group of parsed.groups) validateTreeGroup(group)
  }
  requireValue(Array.isArray(parsed.nodes), 'nodes must be an array')
  for (const node of parsed.nodes) validateNode(node)
  validateGraph(parsed.nodes as TreeNode[])
  validateGroups(parsed as unknown as TreeWorkspace)
}

/** Validate untrusted local snapshots without inferring completion or resuming interrupted work. */
export function parseTreeWorkspace(json: string): TreeWorkspace {
  const parsed = normalizeLegacyWorkspace(parseJson(json))
  validateWorkspace(parsed)
  return parsed as TreeWorkspace
}

function sortedJson(value: unknown): unknown {
  if (Array.isArray(value)) return value.map((item: unknown) => sortedJson(item))
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(Object.entries(value).sort(([left]: [string, unknown], [right]: [string, unknown]) =>
      left.localeCompare(right)).map(([key, item]: [string, unknown]) => [key, sortedJson(item)]))
  }
  return value
}

/**
 * Approval identity excludes only workspace bookkeeping and explicit presentation fields.
 * Evidence (including its timestamps), topology, inputs, target identity, and execution/scoring settings remain significant.
 */
export function treeSemanticSignature(workspace: TreeWorkspace): string {
  const snapshot = parseTreeWorkspace(JSON.stringify(workspace))
  for (const key of ['revision', 'createdAt', 'updatedAt']) Reflect.deleteProperty(snapshot, key)
  for (const node of snapshot.nodes) {
    Reflect.deleteProperty(node, 'position')
    Reflect.deleteProperty(node, 'size')
  }
  const settings = getTreeSettings(workspace)
  for (const key of ['markdown', 'nodeSize', 'edgeStyle', 'stackSamples', 'stackVariants']) {
    Reflect.deleteProperty(settings, key)
  }
  snapshot.settings = settings
  for (const group of snapshot.groups ?? []) {
    Reflect.deleteProperty(group, 'collapsed')
    Reflect.deleteProperty(group, 'activeNodeId')
  }
  return JSON.stringify(sortedJson(snapshot))
}

function normalizeTreeLabels(labels: Record<string, string>, workspaceId: string, nodeId?: string, attemptId?: string): Record<string, string> {
  const next: Record<string, string> = { ...labels, [TREE_WORKSPACE_LABEL]: workspaceId }
  if (nodeId === undefined) {
    Reflect.deleteProperty(next, TREE_NODE_LABEL)
    Reflect.deleteProperty(next, TREE_ATTEMPT_LABEL)
  } else {
    next[TREE_NODE_LABEL] = nodeId
    if (attemptId) next[TREE_ATTEMPT_LABEL] = attemptId
    else Reflect.deleteProperty(next, TREE_ATTEMPT_LABEL)
  }
  return next
}

export function getTreeLabels(workspace: TreeWorkspace, nodeId?: string): Record<string, string> {
  if (nodeId !== undefined) identifier(nodeId, 'node ID')
  const node = nodeId === undefined ? undefined : workspace.nodes.find((item: TreeNode) => item.id === nodeId)
  return normalizeTreeLabels(workspace.labels, workspace.id, nodeId, node ? getCurrentAttemptId(node) : undefined)
}

function buildDraftNode(parentId: string | null, prompt: string, pipeline: TreeConverterSpec[], parentAttemptId?: string): TreeNode {
  return {
    id: newId(),
    parentId,
    parentAttemptId,
    attemptId: newAttemptId(),
    prompt,
    converters: pipeline,
    status: 'draft',
    pruned: false,
    kept: false,
  }
}

function groupCollapsed(workspace: TreeWorkspace, kind: TreeGroup['kind']): boolean {
  const settings = getTreeSettings(workspace)
  return kind === 'sample' ? settings.stackSamples : settings.stackVariants
}

function appendGroup(workspace: TreeWorkspace, kind: TreeGroup['kind'], nodeIds: string[], activeNodeId = nodeIds[0]): void {
  requireValue(nodeIds.length > 0, 'groups require at least one node')
  requireValue(activeNodeId !== undefined, 'groups require an active node')
  const groups = workspace.groups ? [...workspace.groups] : []
  groups.push({
    id: newId(),
    kind,
    nodeIds,
    collapsed: groupCollapsed(workspace, kind),
    activeNodeId,
  })
  workspace.groups = groups
}

function findGroupByNodeId(workspace: TreeWorkspace, nodeId: string): TreeGroup | undefined {
  return workspace.groups?.find((group: TreeGroup) => group.nodeIds.includes(nodeId))
}

function appendSiblingGroupMembers(
  workspace: TreeWorkspace,
  anchorNodeId: string,
  kind: TreeGroup['kind'],
  createdNodeIds: string[],
  includeAnchor: boolean,
): void {
  requireValue(createdNodeIds.length > 0, 'groups require at least one new node')
  const existing = findGroupByNodeId(workspace, anchorNodeId)
  if (existing && kind === 'sample' && existing.kind !== 'sample') {
    existing.nodeIds = existing.nodeIds.filter((id) => id !== anchorNodeId)
    if (existing.activeNodeId === anchorNodeId) existing.activeNodeId = existing.nodeIds[0]
    workspace.groups = workspace.groups?.filter((group) => group.nodeIds.length >= 2)
  } else if (existing) {
    const anchorParentId = getNode(workspace, anchorNodeId).parentId
    requireValue(existing.nodeIds.every((nodeId: string) => getNode(workspace, nodeId).parentId === anchorParentId),
      'grouped nodes must share the same parent')
    const appended = existing.nodeIds.slice()
    for (const nodeId of createdNodeIds) if (!appended.includes(nodeId)) appended.push(nodeId)
    existing.nodeIds = appended
    return
  }
  appendGroup(workspace, kind, includeAnchor ? [anchorNodeId, ...createdNodeIds] : createdNodeIds, includeAnchor ? anchorNodeId : createdNodeIds[0])
}

function importedNodeSignature(node: TreeNode): string {
  return JSON.stringify({
    id: node.id,
    parentId: node.parentId,
    parentAttemptId: node.parentAttemptId,
    attemptId: getCurrentAttemptId(node),
    prompt: node.prompt,
    converters: node.converters,
    status: node.status,
    attackResultId: node.attackResultId,
    conversationId: node.conversationId,
    lastSequence: node.lastSequence,
    messages: node.messages,
    error: node.error,
    importedFromBackend: node.importedFromBackend === true,
  })
}

function validateImportedNode(current: TreeNode, source: TreeNode): void {
  requireValue(source.importedFromBackend === true, `${IMPORTED_NODE_ERROR}.`)
  requireValue(source.status === 'completed' || source.status === 'error', `${IMPORTED_NODE_ERROR}.`)
  requireValue(source.converters.length === 0 && source.forkedFrom === undefined, `${IMPORTED_NODE_ERROR}.`)
  requireValue(source.messages !== undefined && source.attackResultId !== undefined &&
    source.conversationId !== undefined && source.lastSequence !== undefined,
  `${IMPORTED_NODE_ERROR}.`)
  requireValue(importedNodeSignature(current) === importedNodeSignature(source),
    'Imported history conflicts with existing nodes. Reload before importing again.')
}

function cloneValidatedNode(node: TreeNode): TreeNode {
  const cloned = parseJson(JSON.stringify(node))
  validateNode(cloned)
  return cloned as TreeNode
}

function currentAttemptState(node: TreeNode): AttemptLike {
  return {
    attemptId: getCurrentAttemptId(node),
    parentAttemptId: node.parentAttemptId,
    prompt: node.prompt,
    converters: node.converters,
    status: node.status,
    attackResultId: node.attackResultId,
    conversationId: node.conversationId,
    lastSequence: node.lastSequence,
    messages: node.messages,
    error: node.error,
    scoreRuns: node.scoreRuns,
  }
}

/** Projects only observed attempt fields, shared by retry archives, storage, and run receipts. */
export function getCurrentTreeAttempt(node: TreeNode): TreeAttempt | undefined {
  if (!isObservedStatus(node.status)) return undefined
  return { ...currentAttemptState(node), status: node.status }
}

/** Historical and current observed evidence in attempt order; drafts and in-flight states are not results. */
export function getTreeAttempts(node: TreeNode): TreeAttempt[] {
  const current = getCurrentTreeAttempt(node)
  return [...(node.attempts ?? []), ...(current ? [current] : [])]
}

function clearExecution(node: TreeNode, parentAttemptId?: string): void {
  const archived = getCurrentTreeAttempt(node)
  if (archived) node.attempts = [...(node.attempts ?? []), archived]
  node.attemptId = newAttemptId()
  node.parentAttemptId = parentAttemptId
  node.status = 'draft'
  Reflect.deleteProperty(node, 'attackResultId')
  Reflect.deleteProperty(node, 'conversationId')
  Reflect.deleteProperty(node, 'lastSequence')
  Reflect.deleteProperty(node, 'messages')
  Reflect.deleteProperty(node, 'error')
  Reflect.deleteProperty(node, 'scoreRuns')
}

function cloneBranch(
  workspace: TreeWorkspace,
  node: TreeNode,
  rootPrompt: string,
  rootConverters: TreeConverterSpec[],
): TreeNode[] {
  const originals = orderedSubtree(workspace, node.id, false)
  const clonedNodeIds = new Map<string, string>()
  const clonedAttemptIds = new Map<string, string>()
  const clones: TreeNode[] = []
  for (const original of originals) {
    const clonedId = newId()
    const attemptId = newAttemptId()
    clonedNodeIds.set(original.id, clonedId)
    clonedAttemptIds.set(original.id, attemptId)
    const parentId = original.id === node.id
      ? original.parentId
      : clonedNodeIds.get(original.parentId ?? '') ?? null
    const parentAttemptId = parentId === null ? undefined
      : original.id === node.id
        ? original.parentId === null ? undefined : getCurrentAttemptId(getNode(workspace, original.parentId))
        : clonedAttemptIds.get(original.parentId ?? '')
    clones.push({
      ...buildDraftNode(
        parentId,
        original.id === node.id ? rootPrompt : original.prompt,
        original.id === node.id ? rootConverters : original.converters,
        parentAttemptId,
      ),
      id: clonedId,
      attemptId,
      forkedFrom: original.id,
    })
  }
  return clones
}

export function createTreeWorkspace(configuration: Configuration): TreeWorkspace {
  const now = new Date().toISOString()
  const id = newId()
  return parseTreeWorkspace(JSON.stringify({
    ...configuration,
    schemaVersion: 1,
    id,
    revision: 0,
    labels: normalizeTreeLabels(configuration.labels, id),
    nodes: [],
    createdAt: now,
    updatedAt: now,
    settings: DEFAULT_TREE_SETTINGS,
  }))
}

export function isNodeHidden(workspace: TreeWorkspace, nodeId: string): boolean {
  let node = getNode(workspace, nodeId)
  const visited = new Set<string>()
  while (!node.pruned) {
    requireValue(!visited.has(node.id), 'cycle detected')
    visited.add(node.id)
    if (node.parentId === null) return false
    node = getNode(workspace, node.parentId)
  }
  return true
}

function consumeRunBudget(calls: number, node: TreeNode, settings: TreeSettings): number {
  const next = calls + 1 + node.converters.length + (settings.autoScore ? settings.scorers.length : 0)
  requireValue(next <= settings.operationBudget, buildRunBudgetMessage(settings.operationBudget))
  return next
}

function traversalRoots(nodes: TreeNode[], nodeSet?: Set<string>): TreeNode[] {
  return nodes.filter((node: TreeNode) => !nodeSet?.has(node.parentId ?? ''))
}

function orderedNodes(
  workspace: TreeWorkspace,
  roots: TreeNode[],
  traversal: TraversalMode,
  allowedIds?: Set<string>,
): TreeNode[] {
  const children = childrenByParent(workspace)
  const result: TreeNode[] = []
  if (traversal === 'depth-first') {
    const visit = (node: TreeNode): void => {
      result.push(node)
      for (const child of children.get(node.id) ?? []) if (!allowedIds || allowedIds.has(child.id)) visit(child)
    }
    for (const root of roots) visit(root)
    return result
  }
  const queue = [...roots]
  while (queue.length > 0) {
    const current = queue.shift()
    if (!current) continue
    result.push(current)
    for (const child of children.get(current.id) ?? []) if (!allowedIds || allowedIds.has(child.id)) queue.push(child)
  }
  return result
}

function runnableRoots(workspace: TreeWorkspace, rootId?: string): TreeNode[] {
  if (rootId !== undefined) return [getNode(workspace, rootId)]
  return workspace.nodes.filter((node: TreeNode) => node.parentId === null)
}

function sameParentContext(node: TreeNode, parent: TreeNode): boolean {
  return node.parentAttemptId === getCurrentAttemptId(parent)
}

function canTraverseNode(workspace: TreeWorkspace, node: TreeNode): boolean {
  if (isNodeHidden(workspace, node.id) || node.status === 'error' || node.status === 'running') return false
  if (node.parentId === null) return true
  const parent = getNode(workspace, node.parentId)
  if (!sameParentContext(node, parent)) return false
  return parent.status === 'completed' || parent.status === 'draft'
}

/** Observed prompts and evidence are immutable; rewriting is an explicit sibling fork. */
export function applyTreeCommand(workspace: TreeWorkspace, command: TreeCommand): TreeWorkspace {
  const next = parseTreeWorkspace(JSON.stringify(workspace))
  if (command.type === 'autoLayout') {
    for (const node of next.nodes) Reflect.deleteProperty(node, 'position')
    next.updatedAt = new Date().toISOString()
    return parseTreeWorkspace(JSON.stringify(next))
  }
  if (command.type === 'settings') {
    next.settings = normalizeSettings(command.settings)
    next.updatedAt = new Date().toISOString()
    return parseTreeWorkspace(JSON.stringify(next))
  }
  if (command.type === 'group') {
    const group = next.groups?.find((entry: TreeGroup) => entry.id === command.groupId)
    requireValue(group, 'group not found')
    if (command.collapsed !== undefined) group.collapsed = command.collapsed
    if (command.activeNodeId !== undefined) {
      requireValue(group.nodeIds.includes(command.activeNodeId), 'group active node must be a member')
      group.activeNodeId = command.activeNodeId
    }
    next.updatedAt = new Date().toISOString()
    return parseTreeWorkspace(JSON.stringify(next))
  }
  if (command.type === 'add') {
    let parentAttemptId: string | undefined
    if (command.parentId !== null) {
      const parent = getNode(next, command.parentId)
      parentAttemptId = getCurrentAttemptId(parent)
    }
    next.nodes.push(buildDraftNode(command.parentId, command.prompt, command.converters ?? [], parentAttemptId))
    next.updatedAt = new Date().toISOString()
    return parseTreeWorkspace(JSON.stringify(next))
  }

  const node = getNode(next, command.nodeId)
  switch (command.type) {
    case 'edit':
      requireValue(node.status === 'draft', 'observed nodes are immutable; create a new variant')
      assertNoRunningSubtree(next, node.id, 'editing this branch')
      node.prompt = command.prompt
      node.converters = command.converters
      break
    case 'fanOut': {
      requireValue(command.variants.length > 0 && command.variants.length <= MAX_FAN_OUT,
        `fan-out requires 1 to ${MAX_FAN_OUT} variants`)
      const parentAttemptId = node.parentId === null ? undefined : getCurrentAttemptId(getNode(next, node.parentId))
      const created: string[] = []
      for (const variant of command.variants) {
        const createdNode = buildDraftNode(node.parentId, variant.prompt, variant.converters, parentAttemptId)
        next.nodes.push(createdNode)
        created.push(createdNode.id)
      }
      appendSiblingGroupMembers(next, node.id, 'variant', created, false)
      break
    }
    case 'childVariants': {
      requireValue(!isNodeHidden(next, node.id), 'restore hidden branches before continuing them')
      requireValue(node.status !== 'error' && node.status !== 'running', 'only draft or completed nodes can add child variants')
      requireValue(command.variants.length > 0 && command.variants.length <= MAX_FAN_OUT,
        `child variants require 1 to ${MAX_FAN_OUT} variants`)
      const created: string[] = []
      for (const variant of command.variants) {
        const createdNode = buildDraftNode(node.id, variant.prompt, variant.converters, getCurrentAttemptId(node))
        next.nodes.push(createdNode)
        created.push(createdNode.id)
      }
      appendGroup(next, 'variant', created)
      break
    }
    case 'sample': {
      requireValue(!isNodeHidden(next, node.id), 'restore hidden branches before sampling')
      requireValue(node.status !== 'running', 'Recover the recorded result before sampling')
      requireValue(Number.isSafeInteger(command.count) && command.count >= 1 && command.count <= MAX_FAN_OUT,
        `samples require 1 to ${MAX_FAN_OUT} additional attempts`)
      const parentAttemptId = node.parentId === null ? undefined : getCurrentAttemptId(getNode(next, node.parentId))
      const created: string[] = []
      for (let count = 0; count < command.count; count += 1) {
        const createdNode = { ...buildDraftNode(node.parentId, node.prompt, node.converters, parentAttemptId), forkedFrom: node.id }
        next.nodes.push(createdNode)
        created.push(createdNode.id)
      }
      appendSiblingGroupMembers(next, node.id, 'sample', created, true)
      break
    }
    case 'fork':
      requireValue(node.status !== 'running', 'Recover the recorded result before forking')
      assertNoRunningSubtree(next, node.id, 'forking this branch')
      next.nodes.push(...cloneBranch(next, node, command.prompt, command.converters))
      break
    case 'retry': {
      requireValue(!isNodeHidden(next, node.id), 'restore hidden branches before retrying')
      requireValue(node.status !== 'running', 'Recover the recorded result before retrying')
      requireValue(node.status === 'completed' || node.status === 'error', 'only completed or failed nodes can be retried')
      assertNoRunningSubtree(next, node.id, 'retrying this branch')
      const targets = orderedSubtree(next, node.id, true)
      const newAttempts = new Map<string, string>()
      for (const target of targets) {
        const parentId = target.parentId
        const parentAttemptId = parentId === null ? undefined
          : newAttempts.get(parentId) ?? getCurrentAttemptId(getNode(next, parentId))
        clearExecution(target, parentAttemptId)
        newAttempts.set(target.id, getCurrentAttemptId(target))
      }
      break
    }
    case 'prune':
      requireValue(node.status !== 'running', 'Running branches cannot be pruned')
      assertNoRunningSubtree(next, node.id, 'pruning this branch')
      node.pruned = command.pruned
      rehomePrunedGroups(next)
      break
    case 'keep': {
      requireValue(!isNodeHidden(next, node.id), 'restore hidden branches before keeping them')
      const siblings = next.nodes.filter((item: TreeNode) => item.parentId === node.parentId)
      for (const sibling of siblings) assertNoRunningSubtree(next, sibling.id, 'keeping this branch')
      for (const sibling of siblings) {
        sibling.kept = sibling.id === node.id
        sibling.pruned = sibling.id !== node.id
      }
      rehomePrunedGroups(next)
      break
    }
    case 'move':
      validateNodePosition(command.position, 'position')
      node.position = command.position
      break
    case 'resize':
      if (command.size === undefined) Reflect.deleteProperty(node, 'size')
      else {
        validateNodeSize(command.size, 'node size')
        node.size = { ...command.size }
      }
      if (command.position !== undefined) {
        validateNodePosition(command.position, 'position')
        node.position = { ...command.position }
      }
      break
    case 'score': {
      requireValue(getCurrentAttemptId(node) === command.attemptId, 'score runs can be attached only to the current attempt')
      requireValue(isObservedStatus(node.status) && node.messages !== undefined, 'only observed attempts can receive score runs')
      validateTreeScoreRun(command.result, evidencePieceIds(node.messages))
      node.scoreRuns = [...(node.scoreRuns ?? []), command.result]
      break
    }
    case 'importContinuation': {
      const source = getNode(next, command.nodeId)
      requireValue(source.messages !== undefined && source.attackResultId !== undefined &&
        source.conversationId !== undefined && source.lastSequence !== undefined,
      `${IMPORTED_NODE_ERROR}.`)
      const initialCount = next.nodes.length
      const existing = new Map(next.nodes.map((entry: TreeNode) => [entry.id, entry]))
      const added = new Map<string, TreeNode>()
      for (const rawImported of command.nodes) {
        const imported = cloneValidatedNode(rawImported)
        const parent = added.get(imported.parentId ?? '') ?? existing.get(imported.parentId ?? '')
        requireValue(parent !== undefined, `${IMPORTED_NODE_ERROR}; imported parents must exist.`)
        requireValue(isDescendant(next, parent, command.nodeId), `${IMPORTED_NODE_ERROR}; imported nodes must extend the selected lineage.`)
        requireValue(imported.parentAttemptId === getCurrentAttemptId(parent),
          `${IMPORTED_NODE_ERROR}; imported nodes must reference the parent current attempt.`)
        requireValue(imported.attackResultId === source.attackResultId && imported.conversationId === source.conversationId,
          `${IMPORTED_NODE_ERROR}; imported nodes must keep the source backend IDs.`)
        const conflict = existing.get(imported.id)
        if (conflict) {
          validateImportedNode(conflict, imported)
          continue
        }
        added.set(imported.id, imported)
        existing.set(imported.id, imported)
        next.nodes.push(imported)
      }
      if (next.nodes.length === initialCount) return parseTreeWorkspace(JSON.stringify(next))
      break
    }
    default:
      throw new Error('Invalid conversation tree command')
  }
  next.updatedAt = new Date().toISOString()
  return parseTreeWorkspace(JSON.stringify(next))
}

function rehomePrunedGroups(workspace: TreeWorkspace): void {
  for (const group of workspace.groups ?? []) {
    if (isNodeHidden(workspace, group.activeNodeId)) {
      const member = group.nodeIds.find((id: string) => !isNodeHidden(workspace, id))
      if (member) group.activeNodeId = member
    }
  }
}

export function getRunNodeIds(workspace: TreeWorkspace, rootId?: string): string[] {
  const validated = parseTreeWorkspace(JSON.stringify(workspace))
  if (rootId !== undefined) getNode(validated, rootId)
  const settings = getTreeSettings(validated)
  const queued = new Set<string>()
  const result: string[] = []
  let calls = 0
  const roots = runnableRoots(validated, rootId)
  const ordered = orderedNodes(validated, roots, settings.traversal)
  for (const node of ordered) {
    if (queued.has(node.id)) continue
    if (rootId !== undefined && !isDescendant(validated, node, rootId)) continue
    if (!canTraverseNode(validated, node)) continue
    if (node.status === 'completed') continue
    if (node.status !== 'draft') continue
    if (node.parentId !== null) {
      const parent = getNode(validated, node.parentId)
      if (parent.status !== 'completed' && !queued.has(parent.id)) continue
      if (!sameParentContext(node, parent)) continue
    }
    queued.add(node.id)
    result.push(node.id)
    calls = consumeRunBudget(calls, node, settings)
  }
  return result
}

export function getNewRunNodeIds(workspace: TreeWorkspace, nodeIds: string[]): string[] {
  const validated = parseTreeWorkspace(JSON.stringify(workspace))
  requireValue(Array.isArray(nodeIds), 'Run contains invalid node IDs')
  if (nodeIds.length === 0) return []
  const requested = new Set<string>()
  for (const nodeId of nodeIds) {
    requireValue(!requested.has(nodeId), 'Run cannot contain duplicate nodes')
    const node = getNode(validated, nodeId)
    requireValue(!isNodeHidden(validated, nodeId), 'Pruned branches cannot run')
    requireValue(node.status === 'draft', 'Only pristine drafts can run. Create a new variant of observed nodes.')
    requested.add(nodeId)
  }
  for (const nodeId of requested) {
    let current = getNode(validated, nodeId)
    while (current.parentId !== null) {
      const parent = getNode(validated, current.parentId)
      if (requested.has(parent.id)) {
        requireValue(parent.status === 'draft', RUN_PARENT_FIRST_MESSAGE)
      } else {
        requireValue(parent.status === 'completed', RUN_PARENT_FIRST_MESSAGE)
      }
      requireValue(sameParentContext(current, parent), 'Run the parent first with its current response history')
      current = parent
    }
  }
  const roots = traversalRoots(validated.nodes.filter((node: TreeNode) => requested.has(node.id)), requested)
  const settings = getTreeSettings(validated)
  const result = orderedNodes(validated, roots, settings.traversal, requested).map((node: TreeNode) => node.id)
  let calls = 0
  for (const nodeId of result) calls = consumeRunBudget(calls, getNode(validated, nodeId), settings)
  return result
}

/** Portable plans contain declarations only, never target configuration or observed responses. */
export function exportTreePlan(workspace: TreeWorkspace): string {
  const validated = parseTreeWorkspace(JSON.stringify(workspace))
  return JSON.stringify({
    schemaVersion: 1,
    name: validated.name,
    steps: validated.nodes.filter((node: TreeNode) => !isNodeHidden(validated, node.id))
      .map((node: TreeNode) => ({ id: node.id, parentId: node.parentId, prompt: node.prompt, converters: node.converters })),
  }, null, 2)
}

export function importTreePlan(json: string, configuration: Configuration): TreeWorkspace {
  const plan = record(parseJson(json), 'plan')
  onlyKeys(plan, ['schemaVersion', 'name', 'steps'], 'plan')
  requireValue(plan.schemaVersion === 1, 'unsupported plan version')
  text(plan.name, 'plan name', false, MAX_IDENTIFIER_LENGTH)
  requireValue(Array.isArray(plan.steps) && plan.steps.length <= MAX_TREE_NODES, `plan allows at most ${MAX_TREE_NODES} steps`)
  const steps = plan.steps.map((item: unknown): TreeNode => {
    const step = record(item, 'step')
    onlyKeys(step, ['id', 'parentId', 'prompt', 'converters'], 'step')
    const node = {
      ...step,
      status: 'draft',
      pruned: false,
      kept: false,
      attemptId: typeof step.id === 'string' ? initialAttemptId(step.id) : step.id,
    }
    validateNode(node)
    return node
  })
  validateGraph(steps)
  const ids = new Map(steps.map((node: TreeNode) => [node.id, newId()]))
  const attemptIds = new Map(steps.map((node: TreeNode) => [node.id, newAttemptId()]))
  const workspace = createTreeWorkspace(configuration)
  workspace.nodes = steps.map((node: TreeNode) => ({
    ...node,
    id: ids.get(node.id) ?? newId(),
    attemptId: attemptIds.get(node.id) ?? newAttemptId(),
    parentId: node.parentId === null ? null : ids.get(node.parentId) ?? null,
    parentAttemptId: node.parentId === null ? undefined : attemptIds.get(node.parentId),
  }))
  return parseTreeWorkspace(JSON.stringify(workspace))
}
