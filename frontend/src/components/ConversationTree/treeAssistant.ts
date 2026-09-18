import type {
  TreeAssistantContext, TreeAssistantGrant, TreeAssistantMutation, TreeAssistantPlanStep, TreeAssistantPrecondition,
  TreeAssistantPreparedProposal, TreeAssistantProposal, TreeAssistantReview, TreeGroup, TreeNode, TreePreparedChange, TreeWorkspace,
} from '@/types'

import { prepareTreeChange } from './treeActions'
import { getCurrentAttemptId, getNewRunNodeIds, getTreeSettings, isNodeHidden, parseTreeWorkspace, treeSemanticSignature } from './treeModel'
import { captureTreeUndo } from './treeUndo'

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

export function captureAssistantPrecondition(workspace: TreeWorkspace): TreeAssistantPrecondition {
  return Object.freeze({ workspaceId: workspace.id, baseRevision: workspace.revision, semanticSignature: treeSemanticSignature(workspace) })
}

/** Legacy proposals remain revision-bound; only a host snapshot authorizes presentation-only drift. */
export function validateAssistantPrecondition(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, precondition?: TreeAssistantPrecondition,
): void {
  requireCondition(proposal.workspace_id === workspace.id && (precondition
    ? precondition.workspaceId === workspace.id && precondition.baseRevision === proposal.base_revision
      && precondition.baseRevision <= workspace.revision && precondition.semanticSignature === treeSemanticSignature(workspace)
    : proposal.base_revision === workspace.revision),
    'The tree changed since this proposal. Ask the assistant to re-plan against the current revision.')
}

export function validatePreparedAssistantProposal(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, review: TreeAssistantPreparedProposal,
): void {
  validateAssistantPrecondition(workspace, proposal, review.precondition)
  requireCondition(proposal.status === 'pending' && review.proposalId === proposal.id && review.kind === proposal.action.kind
    && review.actionSignature === JSON.stringify(proposal.action),
    'The proposal no longer matches the reviewed action.')
}

function freezeReview<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) freezeReview(child)
    Object.freeze(value)
  }
  return value
}

/** Revalidate at the save boundary without re-running allocation commands or losing newer presentation. */
export function applyAssistantReview(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, review?: TreeAssistantPreparedProposal,
): TreeAssistantPreparedProposal {
  const prepared = review ?? prepareAssistantProposal(workspace, proposal)
  validatePreparedAssistantProposal(workspace, proposal, prepared)
  if (!('workspace' in prepared)) return prepared
  const latest = parseTreeWorkspace(JSON.stringify(workspace))
  const liveNodes = new Map(latest.nodes.map((node: TreeNode) => [node.id, node]))
  const liveGroups = new Map(latest.groups?.map((group: TreeGroup) => [group.id, group]))
  const { markdown, nodeSize, edgeStyle, stackSamples, stackVariants } = getTreeSettings(latest)
  const rebased = parseTreeWorkspace(JSON.stringify({
    ...prepared.workspace,
    revision: latest.revision, createdAt: latest.createdAt, updatedAt: latest.updatedAt,
    settings: { ...getTreeSettings(prepared.workspace), markdown, nodeSize, edgeStyle, stackSamples, stackVariants },
    nodes: prepared.workspace.nodes.map((node: TreeNode) => {
      const live = liveNodes.get(node.id)
      return live ? { ...node, position: live.position, size: live.size } : node
    }),
    groups: prepared.workspace.groups?.map((group: TreeGroup) => {
      const live = liveGroups.get(group.id)
      if (!live) return group
      const activeNodeId = [live.activeNodeId, group.activeNodeId, ...group.nodeIds]
        .find((id: string) => group.nodeIds.includes(id) && !isNodeHidden(prepared.workspace, id)) ?? group.activeNodeId
      return { ...group, collapsed: live.collapsed, activeNodeId }
    }),
  }))
  const change: TreePreparedChange = {
    ...prepared.change, workspace: rebased, undo: prepared.undo ? captureTreeUndo(latest, rebased) : null,
  }
  return freezeReview({ ...prepared, ...change, change, preparedRevision: latest.revision })
}

/** Required-review form for rebasing the exact approved candidate inside the host transaction. */
export function rebasePreparedAssistantProposal(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, review: TreeAssistantPreparedProposal,
): TreeAssistantPreparedProposal {
  return applyAssistantReview(workspace, proposal, review)
}

/** Pure, immutable review: application consumes this same candidate and its generated node IDs. */
export function prepareAssistantProposal(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, precondition?: TreeAssistantPrecondition,
): TreeAssistantPreparedProposal {
  validateAssistantPrecondition(workspace, proposal, precondition)
  requireCondition(proposal.status === 'pending', 'This proposal has already been resolved.')
  const action = proposal.action
  requireCondition(action && typeof action === 'object', 'Invalid assistant action.')
  const settings = getTreeSettings(workspace)
  const base: TreeAssistantReview = {
    proposalId: proposal.id, actionSignature: JSON.stringify(action),
    precondition: precondition ?? captureAssistantPrecondition(workspace), preparedRevision: workspace.revision,
    affectedNodeIds: [], addedNodeIds: [], layoutChanged: false, undo: null,
    changes: [], nodeIds: [], operations: 0, description: '',
  }
  function review(value: TreeAssistantPreparedProposal): TreeAssistantPreparedProposal {
    const candidate = 'workspace' in value ? value.workspace : workspace
    const withChanges = { ...value, changes: value.affectedNodeIds.map((nodeId: string) => ({
      nodeId, before: workspace.nodes.find((node) => node.id === nodeId), after: candidate.nodes.find((node) => node.id === nodeId),
      beforeHidden: workspace.nodes.some((node) => node.id === nodeId) ? isNodeHidden(workspace, nodeId) : undefined,
      afterHidden: candidate.nodes.some((node) => node.id === nodeId) ? isNodeHidden(candidate, nodeId) : undefined,
    })) }
    const detached = JSON.parse(JSON.stringify(withChanges)) as TreeAssistantPreparedProposal
    return freezeReview('workspace' in detached
      ? { ...detached, change: { ...detached.change, workspace: detached.workspace } } : detached)
  }
  if (action.kind === 'plan') {
    requireCondition(Object.keys(action).every((key) => ['kind', 'steps', 'run'].includes(key)) && typeof action.run === 'boolean', 'Invalid plan action.')
    requireCondition(Array.isArray(action.steps) && action.steps.length > 0 && action.steps.length <= 20, 'A plan must contain 1 to 20 ordered steps.')
    let candidate = workspace
    const stepNodes = new Map<string, string>()
    const affected = new Set<string>()
    let selectionId: string | undefined
    let layoutChanged = false
    for (const step of action.steps) {
      requireCondition(step && typeof step === 'object' && Object.keys(step).every((key) => ['id', 'parent', 'prompt', 'converters'].includes(key)),
        'Invalid plan step.')
      requireCondition(typeof step.id === 'string' && step.id.length > 0 && step.id.length <= 200 && !stepNodes.has(step.id)
        && !workspace.nodes.some((node) => node.id === step.id), 'Plan step IDs must be unique and distinct from existing node IDs.')
      const parentId = resolvePlanParent(step, stepNodes, workspace)
      const change = prepareTreeChange(candidate, [{ type: 'add', parentId, prompt: step.prompt, converters: step.converters }])
      candidate = change.workspace
      for (const id of change.affectedNodeIds) affected.add(id)
      selectionId = change.selectionId
      layoutChanged ||= change.layoutChanged
      stepNodes.set(step.id, change.addedNodeIds[0])
    }
    const nodeIds = [...stepNodes.values()]
    const runnableIds = action.run ? getNewRunNodeIds(candidate, nodeIds) : []
    let operations = 0
    if (action.run) {
      operations = candidate.nodes.filter((node) => nodeIds.includes(node.id)).reduce((count, node) =>
        count + 1 + node.converters.length + (settings.autoScore ? settings.scorers.length : 0), 0)
    }
    const change: TreePreparedChange = {
      workspace: candidate, addedNodeIds: nodeIds, affectedNodeIds: [...affected], selectionId, layoutChanged, undo: null,
    }
    return review({
      ...base, ...change, change, kind: 'plan', nodeIds: runnableIds,
      operations, run: action.run,
      description: `${nodeIds.length} ordered draft steps. ${action.run ? `${operations} planned operations to run only those new steps.` : 'No model calls; creates drafts only.'}`,
    })
  }
  if (action.kind === 'mutate') {
    requireCondition(Object.keys(action).every((key) => key === 'kind' || key === 'commands'), 'Invalid mutation action fields.')
    requireCondition(Array.isArray(action.commands) && action.commands.length > 0 && action.commands.length <= 20,
      'A proposal must contain between 1 and 20 edits.')
    for (const command of action.commands) validateCommand(command)
    const change = prepareTreeChange(workspace, action.commands)
    return review({
      ...base, ...change, change, kind: 'mutate',
      description: `${action.commands.length} tree edits; ${change.addedNodeIds.length} new nodes. No model calls. Auto-run is suppressed.`,
    })
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
  return review({
    ...base, kind: action.kind, nodeIds, affectedNodeIds: nodeIds, operations,
    description: `${nodeIds.length} ${action.kind === 'run' ? 'target sends' : 'responses to score'}; ${operations} planned operations. Provider-internal calls may cost more.`,
  })
}

function resolvePlanParent(step: TreeAssistantPlanStep, stepNodes: Map<string, string>, workspace: TreeWorkspace): string | null {
  if (step.parent === null) return null
  const parent = step.parent
  requireCondition(parent && typeof parent === 'object' && Object.keys(parent).length === 1, 'Plan parents must reference exactly one node or earlier step.')
  if ('step_id' in parent) {
    const id = stepNodes.get(parent.step_id)
    requireCondition(id, 'Plan steps may reference only earlier steps; cycles and forward references are not allowed.')
    return id
  }
  requireCondition('node_id' in parent && workspace.nodes.some((node) => node.id === parent.node_id), 'Unknown plan parent node.')
  return parent.node_id
}

/** Scope checks authorize effects, not merely the node named by a command. */
export function validateAutonomousProposal(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, grant: TreeAssistantGrant,
  prepared: TreeAssistantPreparedProposal = prepareAssistantProposal(workspace, proposal),
): number {
  validatePreparedAssistantProposal(workspace, proposal, prepared)
  const scope = new Set([grant.root_node_id])
  requireCondition(workspace.nodes.some((node) => node.id === grant.root_node_id), 'The granted subtree no longer exists.')
  for (let changed = true; changed;) {
    changed = false
    for (const node of workspace.nodes) if (node.parentId && scope.has(node.parentId) && !scope.has(node.id)) {
      scope.add(node.id); changed = true
    }
  }
  const action = proposal.action
  if (action.kind === 'plan') {
    for (const step of action.steps) {
      requireCondition(step.parent !== null && (!('node_id' in step.parent) || scope.has(step.parent.node_id)), 'Plan escapes the granted subtree.')
    }
  } else if (action.kind === 'mutate') {
    for (const command of action.commands) {
      const id = command.type === 'add' ? command.parentId : command.nodeId
      requireCondition(id && scope.has(id), 'Mutation escapes the granted subtree.')
      if (['sample', 'fork', 'keep'].includes(command.type)) {
        requireCondition(id !== grant.root_node_id, 'This operation would affect siblings outside the granted subtree.')
      }
    }
  } else requireCondition(action.node_ids.every((id) => scope.has(id)), 'Execution escapes the granted subtree.')
  requireCondition(prepared.operations <= grant.remaining_operations, 'The autonomy operation budget is exhausted.')
  return prepared.operations
}

export function previewAssistantProposal(
  workspace: TreeWorkspace, proposal: TreeAssistantProposal, precondition?: TreeAssistantPrecondition,
): { description: string; operations: number } {
  const { description, operations } = prepareAssistantProposal(workspace, proposal, precondition)
  return { description, operations }
}
