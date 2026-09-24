import type { TreeCommand, TreeGroup, TreeNode, TreePreparedChange, TreeWorkspace } from '@/types'

import { applyTreeCommand, isNodeHidden, parseTreeWorkspace } from './treeModel'
import { captureTreeUndo } from './treeUndo'

export { treeSemanticSignature } from './treeModel'

function isUndoable(command: TreeCommand): boolean {
  return ['edit', 'prune', 'keep', 'move', 'resize', 'autoLayout'].includes(command.type) ||
    (command.type === 'group' && command.collapsed !== undefined)
}

/** Prepares one atomic local change, without persisting, authorizing, or dispatching requests. */
export function prepareTreeChange(workspace: TreeWorkspace, commands: TreeCommand[]): TreePreparedChange {
  const original = parseTreeWorkspace(JSON.stringify(workspace))
  let next = original
  let selectionId: string | undefined
  let layoutChanged = false
  for (const command of commands) {
    const before = next
    next = applyTreeCommand(next, command)
    if (command.type === 'group' && command.collapsed === false) {
      next = applyTreeCommand(next, { type: 'autoLayout' })
    }
    layoutChanged ||= command.type === 'autoLayout' || (command.type === 'group' && command.collapsed === false)
    const existingIds = new Set(before.nodes.map((node: TreeNode) => node.id))
    const added = next.nodes.filter((node: TreeNode) => !existingIds.has(node.id))
    if (command.type === 'group' && command.activeNodeId) selectionId = command.activeNodeId
    if (command.type === 'retry' || command.type === 'sample') selectionId = command.nodeId
    else if (added.length) {
      selectionId = added.find((node: TreeNode) => !('nodeId' in command) || node.forkedFrom === command.nodeId)?.id ?? added[0].id
    }
  }
  const beforeById = new Map(original.nodes.map((node: TreeNode) => [node.id, node]))
  const addedNodeIds = next.nodes.filter((node: TreeNode) => !beforeById.has(node.id)).map((node: TreeNode) => node.id)
  const affectedNodeIds = next.nodes.filter((node: TreeNode) => {
    const before = beforeById.get(node.id)
    return !before || JSON.stringify(before) !== JSON.stringify(node) ||
      isNodeHidden(original, node.id) !== isNodeHidden(next, node.id) ||
      JSON.stringify(original.groups?.find((group: TreeGroup) => group.nodeIds.includes(node.id))) !==
      JSON.stringify(next.groups?.find((group: TreeGroup) => group.nodeIds.includes(node.id)))
  }).map((node: TreeNode) => node.id)
  const captured = commands.length > 0 && commands.every(isUndoable) ? captureTreeUndo(original, next) : null
  const undo = captured && (captured.nodes.length > 0 || captured.groups) ? captured : null
  return { workspace: next, addedNodeIds, affectedNodeIds, selectionId, layoutChanged, undo }
}
