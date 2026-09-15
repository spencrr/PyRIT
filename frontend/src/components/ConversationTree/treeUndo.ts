import type { TreeGroup, TreeNode, TreeWorkspace } from '@/types'

import { applyTreeCommand, getCurrentAttemptId } from './treeModel'

type EditableField = 'prompt' | 'converters' | 'pruned' | 'kept' | 'position' | 'size'
interface UndoNode {
  id: string
  attemptId: string
  fields: EditableField[]
  before: Pick<TreeNode, EditableField>
  after: Pick<TreeNode, EditableField>
}
export interface TreeUndoEntry {
  workspaceId: string
  nodes: UndoNode[]
  groups?: { before: TreeGroup[]; after: TreeGroup[] }
}
const FIELDS: EditableField[] = ['prompt', 'converters', 'pruned', 'kept', 'position', 'size']

export function captureTreeUndo(before: TreeWorkspace, after: TreeWorkspace): TreeUndoEntry {
  const nodes: UndoNode[] = []
  for (const old of before.nodes) {
    const next = after.nodes.find((node) => node.id === old.id)
    if (!next || getCurrentAttemptId(old) !== getCurrentAttemptId(next)) continue
    const fields = FIELDS.filter((key) => JSON.stringify(old[key]) !== JSON.stringify(next[key]))
    if (fields.length) nodes.push({
      id: old.id, attemptId: getCurrentAttemptId(old), fields,
      before: { prompt: old.prompt, converters: old.converters, pruned: old.pruned, kept: old.kept, position: old.position, size: old.size },
      after: { prompt: next.prompt, converters: next.converters, pruned: next.pruned, kept: next.kept, position: next.position, size: next.size },
    })
  }
  const groups = JSON.stringify(before.groups) !== JSON.stringify(after.groups) ? { before: before.groups ?? [], after: after.groups ?? [] } : undefined
  return { workspaceId: before.id, nodes, groups }
}

/** Reverses local edits only; never resets a provider attempt or removes received evidence. */
export function reverseTreeUndo(workspace: TreeWorkspace, entry: TreeUndoEntry, redo: boolean): TreeWorkspace {
  if (workspace.id !== entry.workspaceId) throw new Error('Undo belongs to another workspace.')
  if (entry.groups) {
    const expected = redo ? entry.groups.before : entry.groups.after
    if (JSON.stringify(workspace.groups ?? []) !== JSON.stringify(expected)) throw new Error('Groups changed since this edit. Undo will not overwrite them.')
  }
  let next = workspace
  for (const change of entry.nodes) {
    const node = next.nodes.find((item) => item.id === change.id)
    if (!node || getCurrentAttemptId(node) !== change.attemptId) throw new Error('This node has a new attempt. Its prior edit cannot be undone.')
    const expected = redo ? change.before : change.after
    const replacement = redo ? change.after : change.before
    if (change.fields.some((key) => JSON.stringify(node[key]) !== JSON.stringify(expected[key]))) {
      throw new Error('The node changed since this edit. Undo will not overwrite it.')
    }
    if (change.fields.includes('prompt') || change.fields.includes('converters')) {
      next = applyTreeCommand(next, { type: 'edit', nodeId: node.id, prompt: replacement.prompt, converters: replacement.converters })
    }
    if (change.fields.includes('pruned')) next = applyTreeCommand(next, { type: 'prune', nodeId: node.id, pruned: replacement.pruned })
    if (change.fields.includes('kept')) next = { ...next, nodes: next.nodes.map((item) => item.id === node.id ? { ...item, kept: replacement.kept } : item) }
    if (change.fields.includes('size')) next = applyTreeCommand(next, { type: 'resize', nodeId: node.id, size: replacement.size })
    if (change.fields.includes('position')) {
      const position = replacement.position
      next = { ...next, nodes: next.nodes.map((item) => item.id === node.id ? { ...item, position } : item) }
    }
  }
  if (entry.groups) {
    next = { ...next, groups: redo ? entry.groups.after : entry.groups.before }
  }
  return next
}
