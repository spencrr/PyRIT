import type {
  TreeAssistantAction, TreeAssistantCheckpoint, TreeAssistantContext, TreeAssistantPrecondition, TreeAssistantProposal, TreeAssistantReceipt,
} from '@/types'

const STORAGE_PREFIX = 'pyrit:tree-assistant:v1:'
const MAX_CHECKPOINT_BYTES = 2 * 1024 * 1024
const MAX_EXPORT_SNAPSHOT_BYTES = 16 * 1024 * 1024
const MAX_CONTEXT_BYTES = 512_000
const MAX_TURNS = 50
const MAX_ARCHIVED_TURNS = 1000
const MAX_EXPORT_ARCHIVED_TURNS = MAX_ARCHIVED_TURNS + MAX_TURNS
const MAX_NODES = 300
const MAX_PROMPT_LENGTH = 32_000
const MAX_TRACE_LENGTH = 64_000
const MAX_TOOL_CALLS = 16
const MAX_TOOL_ARGUMENT_BYTES = 16_000
const MAX_TOOL_RESULT_BYTES = 24_000
const MAX_TURN_TRACE_BYTES = 128_000
const MAX_JSON_VALUES = 60_000
const MAX_JSON_DEPTH = 24
const UNSAFE_KEYS = new Set(['__proto__', 'prototype', 'constructor'])
const CREDENTIAL_KEY = /api[_-]?key|password|secret|credential|authorization|private[_-]?key|^(?:access[_-]?token|refresh[_-]?token|bearer[_-]?token|token|cookie|set-cookie|headers|auth)$/i
const TRACE_CREDENTIAL_KEY = /key|token|secret|password|credential|authorization|headers|cookie/i
const RECOVERY = 'Nothing was changed. Export any remaining local chat before reloading. Preserve the saved record for recovery; clear it only deliberately.'
const COMMAND_KEYS: Record<string, readonly string[]> = {
  add: ['type', 'parentId', 'prompt', 'converters'],
  edit: ['type', 'nodeId', 'prompt', 'converters'],
  fork: ['type', 'nodeId', 'prompt', 'converters'],
  childVariants: ['type', 'nodeId', 'variants'],
  sample: ['type', 'nodeId', 'count'],
  retry: ['type', 'nodeId', 'scope'],
  prune: ['type', 'nodeId', 'pruned'],
  keep: ['type', 'nodeId'],
}

function requireValue(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

function record(value: unknown, label: string): Record<string, unknown> {
  requireValue(value !== null && typeof value === 'object' && !Array.isArray(value), `${label} must be an object`)
  const prototype = Object.getPrototypeOf(value)
  requireValue(prototype === Object.prototype || prototype === null, `${label} must be a plain object`)
  return value as Record<string, unknown>
}

function fields(value: unknown, label: string, required: readonly string[], optional: readonly string[] = []): Record<string, unknown> {
  const object = record(value, label)
  requireValue(required.every((key: string) => Object.prototype.hasOwnProperty.call(object, key))
    && Object.keys(object).every((key: string) => required.includes(key) || optional.includes(key)),
  `${label} has missing or unsupported fields`)
  return object
}

function text(value: unknown, label: string, maximum = MAX_PROMPT_LENGTH, allowEmpty = true): asserts value is string {
  requireValue(typeof value === 'string' && value.length <= maximum && (allowEmpty || value.trim().length > 0),
    `${label} must be ${allowEmpty ? 'a' : 'a nonempty'} string of at most ${maximum} characters`)
}

function identifier(value: unknown, label: string): asserts value is string {
  text(value, label, 256, false)
}

function integer(value: unknown, label: string, minimum = 0, maximum = Number.MAX_SAFE_INTEGER): asserts value is number {
  requireValue(typeof value === 'number' && Number.isSafeInteger(value) && value >= minimum && value <= maximum,
    `${label} must be an integer between ${minimum} and ${maximum}`)
}

function boolean(value: unknown, label: string): void {
  requireValue(typeof value === 'boolean', `${label} must be a boolean`)
}

function list(value: unknown, label: string, maximum: number, minimum = 0): unknown[] {
  requireValue(Array.isArray(value) && value.length >= minimum && value.length <= maximum,
    `${label} must contain ${minimum} to ${maximum} items`)
  return value
}

function identifiers(value: unknown, label: string, maximum: number, minimum = 0): void {
  const items = list(value, label, maximum, minimum)
  for (const item of items) identifier(item, label)
  requireValue(new Set(items).size === items.length, `${label} must be unique`)
}

function byteLength(value: string): number {
  return new TextEncoder().encode(value).length
}

function traceJsonByteLength(value: unknown): number {
  const serialized = JSON.stringify(value)
  let quoted = false
  let escaped = false
  let separators = 0
  for (const character of serialized) {
    if (escaped) { escaped = false; continue }
    if (quoted && character === '\\') { escaped = true; continue }
    if (character === '"') quoted = !quoted
    else if (!quoted && (character === ',' || character === ':')) separators += 1
  }
  // The backend includes a space after each JSON comma and colon in its trace byte limits.
  return byteLength(serialized) + separators
}

function keyFor(workspaceId: string): string {
  requireValue(typeof workspaceId === 'string' && /^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,255}$/.test(workspaceId)
    && !UNSAFE_KEYS.has(workspaceId), 'Invalid assistant workspace ID')
  return `${STORAGE_PREFIX}${workspaceId}`
}

/** Validate before serialization so getters, custom prototypes and non-JSON values cannot disappear silently. */
function validateJson(value: unknown, maximumBytes: number): void {
  let count = 0
  let textLength = 0
  function visit(item: unknown, depth: number, path: string[]): void {
    count += 1
    requireValue(depth <= MAX_JSON_DEPTH && count <= MAX_JSON_VALUES, 'Checkpoint JSON is too deeply nested or complex')
    if (item === null || typeof item === 'boolean') return
    if (typeof item === 'string') {
      const hostSignature = path.length === 3 && path[2] === 'semanticSignature'
        && (path[0] === 'preconditions' || (path[0] === 'executing' && path[1] === 'precondition'))
      text(item, hostSignature ? 'Host semantic signature' : 'Checkpoint text', hostSignature ? maximumBytes : MAX_TRACE_LENGTH)
      textLength += item.length
      requireValue(textLength <= maximumBytes, `Checkpoint exceeds the ${maximumBytes / (1024 * 1024)} MB UTF-8 limit`)
      return
    }
    if (typeof item === 'number') { requireValue(Number.isFinite(item), 'Checkpoint numbers must be finite'); return }
    requireValue(typeof item === 'object', 'Checkpoint contains a non-JSON value')
    if (Array.isArray(item)) {
      requireValue(Object.getPrototypeOf(item) === Array.prototype && item.length <= MAX_JSON_VALUES
        && Object.keys(item).length === item.length
        && Object.keys(item).every((key: string) => /^(?:0|[1-9]\d*)$/.test(key) && Number(key) < item.length),
        'Checkpoint arrays must be dense JSON arrays')
    } else record(item, 'Checkpoint object')
    for (const key of Reflect.ownKeys(item)) {
      if (Array.isArray(item) && key === 'length') continue
      requireValue(typeof key === 'string' && key.length <= 256 && !UNSAFE_KEYS.has(key), 'Checkpoint contains an unsafe object key')
      textLength += key.length
      requireValue(textLength <= maximumBytes, `Checkpoint exceeds the ${maximumBytes / (1024 * 1024)} MB UTF-8 limit`)
      const descriptor = Object.getOwnPropertyDescriptor(item, key)
      requireValue(descriptor && 'value' in descriptor && descriptor.enumerable, 'Checkpoint contains a non-JSON property')
      // Optional fields constructed in memory commonly have an undefined value; JSON omits these.
      if (descriptor.value === undefined && !Array.isArray(item)) continue
      visit(descriptor.value, depth + 1, [...path, key])
    }
  }
  visit(value, 0, [])
}

/** Guard structured fields, not transcript text. Only recorded tool arguments may contain redaction markers. */
function rejectCredentials(value: unknown, allowRedacted: boolean = false): void {
  if (Array.isArray(value)) {
    for (const child of value) rejectCredentials(child, allowRedacted)
  } else if (value !== null && typeof value === 'object') {
    for (const [key, child] of Object.entries(value)) {
      const credentialKey = CREDENTIAL_KEY.test(key) || (allowRedacted && TRACE_CREDENTIAL_KEY.test(key.replace(/[_-]/g, '')))
      requireValue(!credentialKey || (allowRedacted && (child === '[redacted]' || child === '[REDACTED]')),
        'Credentials and authentication headers must not be stored in assistant checkpoints')
      rejectCredentials(child, allowRedacted)
    }
  }
}

function converters(value: unknown): void {
  for (const item of list(value, 'Converter pipeline', 10)) {
    const converter = fields(item, 'Converter', ['type', 'params'])
    identifier(converter.type, 'Converter type')
    const params = record(converter.params, 'Converter parameters')
    rejectCredentials(params)
    requireValue(Object.keys(params).length <= 30 && byteLength(JSON.stringify(converter)) <= 16_000,
      'Converter parameters exceed their size limit')
  }
}

function validateAction(value: unknown): asserts value is TreeAssistantAction {
  const action = record(value, 'Action')
  if (action.kind === 'run' || action.kind === 'score') {
    fields(action, 'Execution action', ['kind', 'node_ids'])
    identifiers(action.node_ids, 'Action node IDs', MAX_NODES, 1)
  } else if (action.kind === 'plan') {
    fields(action, 'Plan action', ['kind', 'steps', 'run'])
    boolean(action.run, 'Plan run flag')
    const stepIds = new Set<string>()
    for (const item of list(action.steps, 'Plan steps', 20, 1)) {
      const step = fields(item, 'Plan step', ['id', 'parent', 'prompt', 'converters'])
      text(step.id, 'Step ID', 200, false)
      requireValue(!stepIds.has(step.id), 'Plan step IDs must be unique')
      if (step.parent !== null) {
        const parent = record(step.parent, 'Plan parent')
        if (Object.prototype.hasOwnProperty.call(parent, 'node_id')) {
          fields(parent, 'Node parent', ['node_id'])
          identifier(parent.node_id, 'Parent node ID')
        } else {
          fields(parent, 'Step parent', ['step_id'])
          identifier(parent.step_id, 'Parent step ID')
          requireValue(stepIds.has(parent.step_id), 'Plan parents must reference earlier steps')
        }
      }
      text(step.prompt, 'Step prompt')
      converters(step.converters)
      stepIds.add(step.id)
    }
  } else {
    requireValue(action.kind === 'mutate', 'Unknown assistant action')
    fields(action, 'Mutation action', ['kind', 'commands'])
    for (const item of list(action.commands, 'Mutation commands', 20, 1)) {
      const command = record(item, 'Command')
      requireValue(typeof command.type === 'string' && Object.prototype.hasOwnProperty.call(COMMAND_KEYS, command.type), 'Unknown assistant command')
      const required = COMMAND_KEYS[command.type].filter((key: string) => command.type !== 'add' || key !== 'converters')
      fields(command, 'Command', required, command.type === 'add' ? ['converters'] : [])
      if (command.type === 'add') {
        if (command.parentId !== null) identifier(command.parentId, 'Parent node ID')
      } else identifier(command.nodeId, 'Node ID')
      if (['add', 'edit', 'fork'].includes(command.type)) text(command.prompt, 'Command prompt')
      if (command.converters !== undefined) converters(command.converters)
      if (command.type === 'sample') integer(command.count, 'Sample count', 1, 20)
      if (command.type === 'retry') requireValue(command.scope === 'node' || command.scope === 'subtree', 'Unknown retry scope')
      if (command.type === 'prune') boolean(command.pruned, 'Pruned flag')
      if (command.type === 'childVariants') {
        for (const variant of list(command.variants, 'Prompt variants', 20, 1)) {
          const entry = fields(variant, 'Prompt variant', ['prompt', 'converters'])
          text(entry.prompt, 'Variant prompt')
          converters(entry.converters)
        }
      }
    }
  }
}

function validateReceipt(value: unknown): asserts value is TreeAssistantReceipt {
  const receipt = fields(value, 'Receipt', ['status', 'revision', 'detail'])
  requireValue(['applied', 'rejected', 'failed'].includes(String(receipt.status)), 'Unknown receipt status')
  integer(receipt.revision, 'Receipt revision')
  text(receipt.detail, 'Receipt detail', 2000)
}

function validateProposal(value: unknown, workspaceId: string): asserts value is TreeAssistantProposal {
  const proposal = fields(value, 'Proposal', ['id', 'workspace_id', 'base_revision', 'summary', 'action', 'status'], ['result'])
  identifier(proposal.id, 'Proposal ID')
  requireValue(proposal.workspace_id === workspaceId, 'Proposal belongs to a different workspace')
  integer(proposal.base_revision, 'Proposal base revision')
  text(proposal.summary, 'Proposal summary', 2000, false)
  validateAction(proposal.action)
  if (proposal.status === 'pending') requireValue(proposal.result == null, 'Pending proposal must not have a receipt')
  else {
    validateReceipt(proposal.result)
    requireValue(proposal.status === proposal.result.status && proposal.result.revision >= proposal.base_revision,
      'Proposal status or revision is inconsistent with its receipt')
  }
}

function validateContext(value: unknown, workspaceId: string): asserts value is TreeAssistantContext {
  const context = fields(value, 'Pending message context', [
    'workspace_id', 'revision', 'name', 'objective', 'target_registry_name', 'target_identifier_hash', 'selected_node_id', 'nodes', 'settings',
  ], ['autonomy'])
  requireValue(context.workspace_id === workspaceId, 'Pending message belongs to a different workspace')
  integer(context.revision, 'Context revision')
  text(context.name, 'Context name', 1000)
  text(context.objective, 'Objective')
  identifier(context.target_registry_name, 'Target registry name')
  identifier(context.target_identifier_hash, 'Target identifier hash')
  const settings = fields(context.settings, 'Context settings', ['traversal', 'concurrency', 'operation_budget', 'scorer_ids'])
  requireValue(settings.traversal === 'breadth-first' || settings.traversal === 'depth-first', 'Unknown traversal')
  requireValue([1, 2, 4].includes(Number(settings.concurrency)) && typeof settings.concurrency === 'number', 'Invalid concurrency')
  integer(settings.operation_budget, 'Operation budget', 1, 100_000)
  identifiers(settings.scorer_ids, 'Scorer IDs', 30)
  const parents = new Map<string, string | null>()
  for (const item of list(context.nodes, 'Context nodes', MAX_NODES)) {
    const node = fields(item, 'Context node', [
      'id', 'parent_id', 'attempt_id', 'prompt', 'converters', 'status', 'pruned', 'kept',
      'response_preview', 'response_truncated', 'score_summary',
    ], ['error', 'attack_result_id', 'conversation_id', 'last_sequence'])
    identifier(node.id, 'Context node ID')
    identifier(node.attempt_id, 'Attempt ID')
    if (node.parent_id !== null) identifier(node.parent_id, 'Parent node ID')
    requireValue(!parents.has(node.id), 'Context node IDs must be unique')
    parents.set(node.id, node.parent_id)
    text(node.prompt, 'Context prompt')
    converters(node.converters)
    requireValue(['draft', 'running', 'completed', 'error'].includes(String(node.status)), 'Unknown context node status')
    for (const flag of ['pruned', 'kept', 'response_truncated']) boolean(node[flag], flag)
    for (const field of ['response_preview', 'score_summary']) text(node[field], field, 2000)
    if (node.error !== undefined) text(node.error, 'Node error', 2000)
    for (const field of ['attack_result_id', 'conversation_id']) if (node[field] !== undefined) identifier(node[field], field)
    if (node.last_sequence !== undefined) integer(node.last_sequence, 'Last sequence', 0, 1_000_000)
  }
  if (context.selected_node_id !== null) {
    identifier(context.selected_node_id, 'Selected node ID')
    requireValue(parents.has(context.selected_node_id), 'Unknown selected context node')
  }
  for (const [id, parent] of parents) {
    const visited = new Set([id])
    let current = parent
    while (current !== null) {
      requireValue(parents.has(current) && !visited.has(current), 'Unknown parent or cycle in context nodes')
      visited.add(current)
      current = parents.get(current) ?? null
    }
  }
  // Preserve the exact pending request for idempotent retry, not as permission to resume actions.
  if (context.autonomy != null) {
    const historical = fields(context.autonomy, 'Historical autonomy context', ['root_node_id', 'remaining_operations', 'remaining_turns', 'goal'])
    identifier(historical.root_node_id, 'Historical autonomy root')
    requireValue(parents.has(historical.root_node_id), 'Unknown historical autonomy root node')
    integer(historical.remaining_operations, 'Historical operation budget', 0, 100_000)
    integer(historical.remaining_turns, 'Historical turn budget', 0, MAX_TURNS)
    text(historical.goal, 'Historical autonomy goal')
  }
  requireValue(byteLength(JSON.stringify(context)) <= MAX_CONTEXT_BYTES, 'Pending message context exceeds 512000 bytes')
}

function validateTrace(turn: Record<string, unknown>, workspaceId: string): void {
  if (turn.tool_calls !== undefined) {
    const ids = new Set<string>()
    for (const item of list(turn.tool_calls, 'Tool calls', MAX_TOOL_CALLS)) {
      const tool = fields(item, 'Tool call', ['id', 'name', 'arguments', 'result', 'status', 'duration_ms', 'truncated'])
      identifier(tool.id, 'Tool call ID')
      requireValue(!ids.has(tool.id), 'Tool call IDs must be unique within a turn')
      ids.add(tool.id)
      identifier(tool.name, 'Tool name')
      record(tool.arguments, 'Tool arguments')
      rejectCredentials(tool.arguments, true)
      requireValue(traceJsonByteLength(tool.arguments) <= MAX_TOOL_ARGUMENT_BYTES, 'Tool arguments exceed the 16000-byte limit')
      text(tool.result, 'Tool result', MAX_TOOL_RESULT_BYTES)
      requireValue(byteLength(tool.result) <= MAX_TOOL_RESULT_BYTES, 'Tool result exceeds the 24000-byte UTF-8 limit')
      requireValue(tool.status === 'completed' || tool.status === 'error', 'Unknown tool status')
      requireValue(typeof tool.duration_ms === 'number' && tool.duration_ms >= 0
        && Number.isFinite(tool.duration_ms) && tool.duration_ms <= Number.MAX_SAFE_INTEGER, 'Invalid tool duration')
      boolean(tool.truncated, 'Tool truncation flag')
    }
    requireValue(traceJsonByteLength(turn.tool_calls) <= MAX_TURN_TRACE_BYTES, 'Turn tool traces exceed the 128000-byte limit')
  }
  if (turn.context_summary != null) {
    const summary = fields(turn.context_summary, 'Turn context summary', [
      'workspace_id', 'revision', 'selected_node_id', 'node_count', 'model', 'api', 'instructions', 'tools', 'restored',
    ], ['restoration_notice'])
    requireValue(summary.workspace_id === workspaceId, 'Turn context belongs to a different workspace')
    integer(summary.revision, 'Turn context revision')
    integer(summary.node_count, 'Turn context node count', 0, MAX_NODES)
    if (summary.selected_node_id !== null) identifier(summary.selected_node_id, 'Turn selected node ID')
    identifier(summary.model, 'Turn model')
    identifier(summary.api, 'Turn API')
    text(summary.instructions, 'Server instructions', MAX_PROMPT_LENGTH)
    identifiers(summary.tools, 'Available tools', MAX_TOOL_CALLS)
    boolean(summary.restored, 'Restored context flag')
    if (summary.restoration_notice != null) text(summary.restoration_notice, 'Restoration notice', 2000)
  }
  if (turn.usage != null) {
    const usage = fields(turn.usage, 'Token usage', [], ['input_tokens', 'output_tokens', 'total_tokens'])
    for (const [key, value] of Object.entries(usage)) {
      // The wire schema uses null for unreported metrics; frontend optional fields omit them.
      if (value === null) delete usage[key]
      else integer(value, 'Token usage')
    }
  }
}

function validatePrecondition(value: unknown, workspaceId: string): asserts value is TreeAssistantPrecondition {
  const precondition = fields(value, 'Host precondition', ['workspaceId', 'baseRevision', 'semanticSignature'])
  requireValue(precondition.workspaceId === workspaceId, 'Host precondition belongs to a different workspace')
  integer(precondition.baseRevision, 'Host precondition revision')
  text(precondition.semanticSignature, 'Host semantic signature', MAX_EXPORT_SNAPSHOT_BYTES, false)
}

function validateCheckpoint(value: unknown, workspaceId: string, maximumArchivedTurns: number): asserts value is TreeAssistantCheckpoint {
  const checkpoint = fields(value, 'Checkpoint', [
    'schemaVersion', 'revision', 'workspaceId', 'savedAt', 'session', 'draft', 'pendingMessage', 'unreported', 'executing',
  ], ['archivedTurns', 'preconditions'])
  requireValue(checkpoint.schemaVersion === 1, 'Unsupported assistant checkpoint schema version')
  requireValue(checkpoint.workspaceId === workspaceId, 'Checkpoint belongs to a different workspace')
  integer(checkpoint.revision, 'Checkpoint revision')
  text(checkpoint.savedAt, 'Saved timestamp', 64, false)
  requireValue(/^\d{4}-\d{2}-\d{2}T/.test(checkpoint.savedAt) && Number.isFinite(Date.parse(checkpoint.savedAt)), 'Invalid saved timestamp')
  text(checkpoint.draft, 'Draft')
  const ids = new Set<string>()
  const proposals = new Map<string, TreeAssistantProposal>()
  const requestRevisions = new Map<string, number | undefined>()
  function uniqueId(id: unknown): void {
    identifier(id, 'Turn or proposal ID')
    requireValue(!ids.has(id), 'Turn and proposal IDs must be unique')
    ids.add(id)
  }
  function validateTurns(value: unknown, label: string, maximum: number): void {
    for (const item of list(value, label, maximum)) {
      const turn = fields(item, 'Turn', ['request_id', 'message', 'reply', 'proposals'], ['tool_calls', 'context_summary', 'usage'])
      uniqueId(turn.request_id)
      requestRevisions.set(turn.request_id as string, undefined)
      text(turn.message, 'Turn message')
      text(turn.reply, 'Turn reply')
      for (const proposal of list(turn.proposals, 'Turn proposals', 1)) {
        validateProposal(proposal, workspaceId)
        uniqueId(proposal.id)
        proposals.set(proposal.id, proposal)
        requestRevisions.set(turn.request_id as string, proposal.base_revision)
      }
      validateTrace(turn, workspaceId)
    }
  }
  if (checkpoint.archivedTurns === undefined) checkpoint.archivedTurns = []
  validateTurns(checkpoint.archivedTurns, 'Archived turns', maximumArchivedTurns)
  if (checkpoint.session !== null) {
    const session = fields(checkpoint.session, 'Session', ['session_id', 'workspace_id', 'model', 'turns'])
    identifier(session.session_id, 'Session ID')
    identifier(session.model, 'Session model')
    requireValue(session.workspace_id === workspaceId, 'Session belongs to a different workspace')
    validateTurns(session.turns, 'Session turns', MAX_TURNS)
  }
  if (checkpoint.pendingMessage !== null) {
    requireValue(checkpoint.session !== null, 'A pending message requires a session')
    const pending = fields(checkpoint.pendingMessage, 'Pending message', ['request_id', 'message', 'context'])
    uniqueId(pending.request_id)
    text(pending.message, 'Pending message text', MAX_PROMPT_LENGTH, false)
    validateContext(pending.context, workspaceId)
    requestRevisions.set(pending.request_id as string, (pending.context as TreeAssistantContext).revision)
  }
  if (checkpoint.preconditions !== undefined) {
    for (const [requestId, precondition] of Object.entries(record(checkpoint.preconditions, 'Host preconditions'))) {
      validatePrecondition(precondition, workspaceId)
      requireValue(requestRevisions.has(requestId) && (requestRevisions.get(requestId) === undefined
        || requestRevisions.get(requestId) === precondition.baseRevision), 'Host precondition must match its recorded request')
    }
  }
  requireValue([checkpoint.pendingMessage, checkpoint.unreported, checkpoint.executing].filter((item: unknown) => item !== null).length <= 1,
    'Checkpoint has conflicting pending operations')
  if (checkpoint.executing !== null) {
    const executing = fields(checkpoint.executing, 'Execution journal', ['proposalId', 'baseRevision'], ['precondition'])
    identifier(executing.proposalId, 'Executing proposal ID')
    integer(executing.baseRevision, 'Execution base revision')
    const proposal = proposals.get(executing.proposalId)
    requireValue(proposal?.status === 'pending' && executing.baseRevision >= proposal.base_revision,
      'Execution journal must reference an existing pending proposal at a consistent revision')
    if (executing.precondition !== undefined) {
      validatePrecondition(executing.precondition, workspaceId)
      requireValue(executing.precondition.baseRevision === proposal.base_revision,
        'Execution precondition must match the proposal revision')
    }
  }
  if (checkpoint.unreported !== null) {
    const unreported = fields(checkpoint.unreported, 'Unreported result', ['proposalId', 'receipt', 'error'])
    identifier(unreported.proposalId, 'Unreported proposal ID')
    validateReceipt(unreported.receipt)
    text(unreported.error, 'Reporting error', MAX_TRACE_LENGTH)
    const proposal = proposals.get(unreported.proposalId)
    requireValue(proposal?.result && proposal.status === unreported.receipt.status
      && proposal.result.revision === unreported.receipt.revision && proposal.result.detail === unreported.receipt.detail,
    'Unreported result must match the recorded proposal receipt')
  }
}

function freeze<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) freeze(child)
    Object.freeze(value)
  }
  return value
}

function malformed(failure: unknown): Error {
  return Object.assign(new Error(`Saved assistant checkpoint is malformed: ${failure instanceof Error ? failure.message : 'invalid snapshot'}. ${RECOVERY}`),
    { cause: failure })
}

function parseSnapshot(serialized: string, expectedWorkspaceId: string, maximumBytes: number, maximumArchivedTurns: number): TreeAssistantCheckpoint {
  try {
    keyFor(expectedWorkspaceId)
    requireValue(typeof serialized === 'string' && serialized.length <= maximumBytes
      && byteLength(serialized) <= maximumBytes, `Checkpoint exceeds the ${maximumBytes / (1024 * 1024)} MB UTF-8 limit`)
    const parsed: unknown = JSON.parse(serialized)
    validateJson(parsed, maximumBytes)
    validateCheckpoint(parsed, expectedWorkspaceId, maximumArchivedTurns)
    return freeze(parsed)
  } catch (failure: unknown) { throw malformed(failure) }
}

/** Normalize missing archives to an empty list; never replay proposals or restore permission to act. */
export function parseAssistantCheckpoint(serialized: string, expectedWorkspaceId: string): TreeAssistantCheckpoint {
  return parseSnapshot(serialized, expectedWorkspaceId, MAX_CHECKPOINT_BYTES, MAX_ARCHIVED_TURNS)
}

function snapshotOf(
  checkpoint: TreeAssistantCheckpoint,
  maximumBytes: number = MAX_CHECKPOINT_BYTES,
  maximumArchivedTurns: number = MAX_ARCHIVED_TURNS,
): TreeAssistantCheckpoint {
  let serialized: string
  try {
    validateJson(checkpoint, maximumBytes)
    record(checkpoint, 'Checkpoint')
    serialized = JSON.stringify(checkpoint)
  } catch (failure: unknown) { throw malformed(failure) }
  return parseSnapshot(serialized, checkpoint.workspaceId, maximumBytes, maximumArchivedTurns)
}

function storageOperation<T>(operation: () => T): T {
  try { return operation() }
  catch (failure: unknown) {
    const detail = failure instanceof Error ? `${failure.name}: ${failure.message}` : 'Storage unavailable'
    throw Object.assign(new Error(`Assistant browser storage failed (${detail}). ${RECOVERY}`), { cause: failure })
  }
}

/** Browser storage is local and unencrypted. Only a missing key returns null; corruption is never reset. */
export function loadAssistantCheckpoint(workspaceId: string): TreeAssistantCheckpoint | null {
  const key = keyFor(workspaceId)
  const serialized = storageOperation((): string | null => localStorage.getItem(key))
  return serialized === null ? null : parseAssistantCheckpoint(serialized, workspaceId)
}

function checkRevision(workspaceId: string, expectedRevision: number, deleting = false): void {
  integer(expectedRevision, 'Expected checkpoint revision')
  const latest = loadAssistantCheckpoint(workspaceId)
  requireValue(latest ? latest.revision === expectedRevision : expectedRevision === 0 && !deleting,
    `Assistant checkpoint revision conflict: the saved chat changed or was deleted. ${RECOVERY}`)
}

/** Synchronous optimistic revision check, not a cross-tab transaction. The returned snapshot is deeply frozen. */
export function saveAssistantCheckpoint(checkpoint: TreeAssistantCheckpoint): TreeAssistantCheckpoint {
  const snapshot = snapshotOf(checkpoint)
  const saved = parseAssistantCheckpoint(JSON.stringify({
    ...snapshot, revision: snapshot.revision + 1, savedAt: new Date().toISOString(),
  }), snapshot.workspaceId)
  const serialized = JSON.stringify(saved)
  const key = keyFor(snapshot.workspaceId)
  checkRevision(snapshot.workspaceId, snapshot.revision)
  storageOperation((): void => { localStorage.setItem(key, serialized) })
  return saved
}

/** Explicit deletion with the same stale-snapshot guard as saving. */
export function deleteAssistantCheckpoint(workspaceId: string, expectedRevision: number): void {
  const key = keyFor(workspaceId)
  checkRevision(workspaceId, expectedRevision, true)
  storageOperation((): void => { localStorage.removeItem(key) })
}

/**
 * Portable evidence, not a restore-API request or permission to act. Server session capabilities are excluded.
 * Free-form transcript text is preserved, not secret-scanned.
 * Recovery accepts source snapshots up to 16 MiB, independently of the 2 MiB storage limit.
 * Up to 1050 archived turns can be exported: one server history window beyond the persistence cap.
 */
export function exportAssistantChat(checkpoint: TreeAssistantCheckpoint): string {
  const snapshot = snapshotOf(checkpoint, MAX_EXPORT_SNAPSHOT_BYTES, MAX_EXPORT_ARCHIVED_TURNS)
  const session = snapshot.session
  const portable = {
    ...snapshot,
    exportedAt: new Date().toISOString(),
    session: session ? { workspace_id: session.workspace_id, model: session.model, turns: session.turns } : null,
  }
  return JSON.stringify(portable, (_key: string, value: unknown): unknown =>
    session && typeof value === 'string' ? value.split(session.session_id).join('[session capability omitted]') : value, 2)
}
