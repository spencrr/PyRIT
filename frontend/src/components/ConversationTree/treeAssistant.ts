import type {
  TreeAssistantContext, TreeAssistantMutation, TreeAssistantProposal, TreeWorkspace,
} from '@/types'

import { applyTreeCommand, getCurrentAttemptId, getNewRunNodeIds, getTreeSettings, isNodeHidden, parseTreeWorkspace } from './treeModel'

const MAX_CONTEXT_BYTES = 512_000
const PREVIEW_LENGTH = 2000
const COMMAND_FIELDS: Record<TreeAssistantMutation['type'], string[]> = {
  add: ['type', 'parentId', 'prompt', 'converters'],
  edit: ['type', 'nodeId', 'prompt', 'converters'],
  childVariants: ['type', 'nodeId', 'variants'],
  sample: ['type', 'nodeId', 'count'],
  fork: ['type', 'nodeId', 'prompt', 'converters'],
  retry: ['type', 'nodeId', 'scope'],
  prune: ['type', 'nodeId', 'pruned'],
  keep: ['type', 'nodeId'],
}

function requireCondition(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message)
}

/** Send saved evaluation context only, without credentials, labels or archived attempts. */
export function createAssistantContext(tree: TreeWorkspace, selectedNodeId: string | null): TreeAssistantContext {
  const workspace = parseTreeWorkspace(JSON.stringify(tree))
  const settings = getTreeSettings(workspace)
  const context: TreeAssistantContext = {
    workspace_id: workspace.id, revision: workspace.revision, name: workspace.name,
    objective: settings.objective, target_registry_name: workspace.targetRegistryName,
    target_identifier_hash: workspace.targetIdentifierHash,
    selected_node_id: workspace.nodes.some((node) => node.id === selectedNodeId) ? selectedNodeId : null,
    settings: {
      traversal: settings.traversal, concurrency: settings.concurrency ?? 1, operation_budget: settings.operationBudget,
      scorer_ids: settings.scorers.map((scorer) => scorer.scorer_id),
    },
    nodes: workspace.nodes.map((node) => {
      const response = node.messages?.filter((message) => message.role === 'assistant')
        .flatMap((message) => message.message_pieces.map((piece) =>
          piece.converted_value_data_type === 'text' ? piece.converted_value : `[${piece.converted_value_data_type}]`)).join('\n') ?? ''
      requireCondition(node.prompt.length <= 32_000, 'A saved prompt exceeds the assistant context limit of 32000 characters.')
      return {
        id: node.id, parent_id: node.parentId, attempt_id: getCurrentAttemptId(node), prompt: node.prompt,
        converters: JSON.parse(JSON.stringify(node.converters)), status: node.status, pruned: isNodeHidden(workspace, node.id), kept: node.kept,
        response_preview: response.slice(0, PREVIEW_LENGTH), response_truncated: response.length > PREVIEW_LENGTH,
        score_summary: JSON.stringify(node.scoreRuns?.slice(-3).map((run) => ({
          scorer_id: run.scorerId, status: run.status,
          scores: run.scores.map((score) => ({ value: score.score_value, type: score.score_type, status: score.status })),
        })) ?? []).slice(0, PREVIEW_LENGTH),
        error: node.error?.slice(0, PREVIEW_LENGTH),
        attack_result_id: node.attackResultId, conversation_id: node.conversationId, last_sequence: node.lastSequence,
      }
    }),
  }
  requireCondition(new Blob([JSON.stringify(context)]).size <= MAX_CONTEXT_BYTES,
    'The saved tree exceeds the assistant context limit. Use a smaller workspace; no partial tree was sent.')
  return context
}

function validateCommand(command: TreeAssistantMutation): void {
  requireCondition(command && typeof command === 'object' && !Array.isArray(command), 'Invalid assistant mutation.')
  const fields = Object.prototype.hasOwnProperty.call(COMMAND_FIELDS, command.type) ? COMMAND_FIELDS[command.type] : undefined
  requireCondition(fields && Object.keys(command).every((key) => fields.includes(key)),
    'Assistant proposals cannot change settings, evidence, or execution state directly.')
  if (command.type === 'retry') requireCondition(command.scope === 'node' || command.scope === 'subtree', 'Invalid retry scope.')
  if (command.type === 'childVariants') {
    requireCondition(Array.isArray(command.variants) && command.variants.every((variant) =>
      variant && typeof variant === 'object' && Object.keys(variant).every((key) => key === 'prompt' || key === 'converters')),
    'Invalid assistant prompt variants.')
  }
}

/** Pure preview and canonical validation; no persistence or provider requests. */
export function prepareAssistantProposal(workspace: TreeWorkspace, proposal: TreeAssistantProposal) {
  requireCondition(proposal.workspace_id === workspace.id && proposal.base_revision === workspace.revision,
    'The tree changed since this proposal. Ask the assistant to re-plan against the current revision.')
  requireCondition(proposal.status === 'pending', 'This proposal has already been resolved.')
  const action = proposal.action
  requireCondition(action && typeof action === 'object', 'Invalid assistant action.')
  const settings = getTreeSettings(workspace)
  if (action.kind === 'mutate') {
    requireCondition(Object.keys(action).every((key) => key === 'kind' || key === 'commands'), 'Invalid mutation action fields.')
    requireCondition(Array.isArray(action.commands) && action.commands.length > 0 && action.commands.length <= 20,
      'A proposal must contain between 1 and 20 edits.')
    let candidate = workspace
    for (const command of action.commands) {
      validateCommand(command)
      candidate = applyTreeCommand(candidate, command)
    }
    return {
      kind: 'mutate' as const, workspace: candidate, operations: 0,
      description: `${action.commands.length} tree edits; ${candidate.nodes.length - workspace.nodes.length} new nodes. No model calls. Auto-run is suppressed.`,
    }
  }
  requireCondition(action.kind === 'run' || action.kind === 'score', 'Unknown assistant action.')
  requireCondition(Object.keys(action).every((key) => key === 'kind' || key === 'node_ids'), 'Invalid execution action fields.')
  requireCondition(Array.isArray(action.node_ids) && action.node_ids.length > 0 && action.node_ids.length <= 300
    && new Set(action.node_ids).size === action.node_ids.length, 'Choose distinct node IDs for this proposal.')
  parseTreeWorkspace(JSON.stringify(workspace))
  const nodeIds = action.kind === 'run' ? getNewRunNodeIds(workspace, action.node_ids) : action.node_ids
  requireCondition(action.kind !== 'score' || settings.scorers.length > 0, 'Select workspace scorers before approving scoring.')
  const nodes = nodeIds.map((id) => {
    const node = workspace.nodes.find((item) => item.id === id)
    requireCondition(node && !isNodeHidden(workspace, id), 'This proposal references a missing or pruned node.')
    if (action.kind === 'score') requireCondition((node.status === 'completed' || node.status === 'error')
      && node.attackResultId && node.conversationId && node.messages?.some((message) => message.role === 'assistant'),
    'Only recorded assistant responses can be scored.')
    return node
  })
  const operations = nodes.reduce((count, node) => count + (action.kind === 'score' ? settings.scorers.length
    : 1 + node.converters.length + (settings.autoScore ? settings.scorers.length : 0)), 0)
  requireCondition(operations <= settings.operationBudget, `This proposal exceeds the workspace budget of ${settings.operationBudget} operations.`)
  return {
    kind: action.kind, nodeIds, operations,
    description: `${nodeIds.length} ${action.kind === 'run' ? 'target sends' : 'responses to score'}; ${operations} planned operations. Provider-internal calls may cost more.`,
  }
}

export function previewAssistantProposal(workspace: TreeWorkspace, proposal: TreeAssistantProposal): { description: string; operations: number } {
  const { description, operations } = prepareAssistantProposal(workspace, proposal)
  return { description, operations }
}
