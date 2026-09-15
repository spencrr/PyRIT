import type { TreeAttempt, TreeNode, TreeWorkspace } from '@/types'

import { getCurrentAttemptId, parseTreeWorkspace } from './treeModel'

export const TREE_STORAGE_PREFIX = 'pyrit:conversation-tree:v1:'

function keyFor(id: string): string {
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,255}$/.test(id)) throw new Error('Invalid workspace ID')
  return `${TREE_STORAGE_PREFIX}${id}`
}

function readStored(key: string, json: string): TreeWorkspace {
  try {
    const workspace = parseTreeWorkspace(json)
    if (keyFor(workspace.id) !== key) throw new Error('Workspace ID does not match its storage key')
    return workspace
  } catch (failure: unknown) {
    throw Object.assign(
      new Error(`Saved conversation tree is malformed; it has not been changed. ${failure instanceof Error ? failure.message : 'Invalid snapshot'}`),
      { cause: failure },
    )
  }
}

function stable(value: unknown): string {
  return JSON.stringify(value)
}

function isObserved(node: TreeNode): node is TreeNode & { status: 'completed' | 'error' } {
  return node.status === 'completed' || node.status === 'error'
}

function currentAttempt(node: TreeNode): TreeAttempt | undefined {
  if (!isObserved(node)) return undefined
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

function nodeInputSignature(node: TreeNode): string {
  return stable({
    id: node.id,
    parentId: node.parentId,
    prompt: node.prompt,
    converters: node.converters,
    forkedFrom: node.forkedFrom,
    importedFromBackend: node.importedFromBackend,
  })
}

function observedAttemptSignature(node: TreeNode): string {
  return stable({
    attemptId: getCurrentAttemptId(node),
    parentAttemptId: node.parentAttemptId,
    status: node.status,
    attackResultId: node.attackResultId,
    conversationId: node.conversationId,
    lastSequence: node.lastSequence,
    messages: node.messages,
    error: node.error,
  })
}

function assertScoreRunsAppendOnly(previous: TreeNode, next: TreeNode): void {
  const stored = previous.scoreRuns ?? []
  const current = next.scoreRuns ?? []
  if (current.length < stored.length) throw new Error('Observed score overlays are append-only')
  for (let index = 0; index < stored.length; index += 1) {
    if (stable(stored[index]) !== stable(current[index])) throw new Error('Observed score overlays are immutable')
  }
}

function assertAttemptHistory(previous: TreeNode, next: TreeNode): void {
  const stored = previous.attempts ?? []
  const current = next.attempts ?? []
  if (current.length < stored.length) throw new Error('Attempt history is append-only')
  for (let index = 0; index < stored.length; index += 1) {
    if (stable(stored[index]) !== stable(current[index])) throw new Error('Historical attempts are immutable')
  }
  if (!isObserved(previous) || getCurrentAttemptId(previous) === getCurrentAttemptId(next)) {
    if (current.length !== stored.length) throw new Error('Unexpected attempt history change')
    return
  }
  if (current.length !== stored.length + 1) throw new Error('Retried attempts must archive the previous observed result')
  const archived = currentAttempt(previous)
  if (!archived || stable(current[current.length - 1]) !== stable(archived)) {
    throw new Error('Retried attempts must preserve the archived result exactly')
  }
}

function assertImmutableEvidence(stored: TreeWorkspace, next: TreeWorkspace): void {
  if (stored.createdAt !== next.createdAt) throw new Error('Workspace creation time is immutable')
  if (stored.nodes.some((node: TreeNode) => node.status !== 'draft')) {
    for (const key of ['targetRegistryName', 'targetIdentifierHash', 'systemPrompt', 'labels'] as const) {
      if (stable(stored[key]) !== stable(next[key])) {
        throw new Error('Observed workspace configuration is immutable. Create a new workspace.')
      }
    }
  }
  for (const previous of stored.nodes) {
    const current = next.nodes.find((node: TreeNode) => node.id === previous.id)
    if (!current) {
      if (previous.status !== 'draft' || (previous.attempts?.length ?? 0) > 0) {
        throw new Error('Observed nodes cannot be removed from a workspace')
      }
      continue
    }
    assertAttemptHistory(previous, current)
    if (previous.status === 'draft') continue
    if (previous.status === 'running') {
      if (getCurrentAttemptId(previous) !== getCurrentAttemptId(current) || nodeInputSignature(previous) !== nodeInputSignature(current)) {
        throw new Error('Running nodes are immutable')
      }
      if (current.status === 'draft' ||
        (previous.attackResultId !== undefined && previous.attackResultId !== current.attackResultId) ||
        (previous.conversationId !== undefined && previous.conversationId !== current.conversationId)) {
        throw new Error('Running execution evidence cannot be reset. Recover or inspect the recorded result before retrying.')
      }
      continue
    }
    if (getCurrentAttemptId(previous) === getCurrentAttemptId(current)) {
      if (nodeInputSignature(previous) !== nodeInputSignature(current) ||
        observedAttemptSignature(previous) !== observedAttemptSignature(current)) {
        throw new Error('Observed evidence is immutable. Create a new variant.')
      }
      assertScoreRunsAppendOnly(previous, current)
      continue
    }
    if (nodeInputSignature(previous) !== nodeInputSignature(current)) {
      throw new Error('Retried attempts must preserve the node prompt and pipeline')
    }
  }
}

/** Browser storage is local, not encrypted, not shared, and can be cleared. Export important work. */
export function listTreeWorkspaces(): TreeWorkspace[] {
  const workspaces: TreeWorkspace[] = []
  for (let index = 0; index < localStorage.length; index += 1) {
    const key = localStorage.key(index)
    if (key?.startsWith(TREE_STORAGE_PREFIX)) {
      const json = localStorage.getItem(key)
      if (json === null) throw new Error('Browser storage changed while listing trees. Reload before editing.')
      workspaces.push(readStored(key, json))
    }
  }
  return workspaces.sort((left: TreeWorkspace, right: TreeWorkspace) =>
    right.updatedAt.localeCompare(left.updatedAt) || left.id.localeCompare(right.id))
}

export function loadTreeWorkspace(id: string): TreeWorkspace {
  const key = keyFor(id)
  const json = localStorage.getItem(key)
  if (json === null) throw new Error('Conversation tree not found. It may have been deleted in another tab.')
  return readStored(key, json)
}

function checkRevision(workspace: TreeWorkspace, key: string): TreeWorkspace | null {
  const json = localStorage.getItem(key)
  const stored = json === null ? null : readStored(key, json)
  if ((!stored && workspace.revision !== 0) || (stored && stored.revision !== workspace.revision)) {
    throw new Error('Conversation tree revision conflict. Reload before editing; no changes were saved.')
  }
  return stored
}

async function withWorkspaceLock<T>(key: string, write: () => T): Promise<T> {
  if (typeof navigator === 'undefined' || typeof navigator.locks?.request !== 'function') {
    throw new Error('Safe tree persistence requires Web Locks (navigator.locks) in a secure browser context. Nothing was written.')
  }
  return navigator.locks.request(key, { mode: 'exclusive' }, write)
}

/** Compare and write under the same cross-tab lock. A failed write never consumes a revision. */
export async function saveTreeWorkspace(workspace: TreeWorkspace): Promise<TreeWorkspace> {
  const snapshot = parseTreeWorkspace(JSON.stringify(workspace))
  const key = keyFor(snapshot.id)
  return withWorkspaceLock(key, (): TreeWorkspace => {
    const stored = checkRevision(snapshot, key)
    if (stored) assertImmutableEvidence(stored, snapshot)
    const saved = parseTreeWorkspace(JSON.stringify({
      ...snapshot,
      revision: snapshot.revision + 1,
      updatedAt: new Date().toISOString(),
    }))
    localStorage.setItem(key, JSON.stringify(saved))
    return saved
  })
}

export async function deleteTreeWorkspace(workspace: TreeWorkspace): Promise<void> {
  const snapshot = parseTreeWorkspace(JSON.stringify(workspace))
  const key = keyFor(snapshot.id)
  await withWorkspaceLock(key, (): void => {
    if (!checkRevision(snapshot, key)) throw new Error('Conversation tree not found; nothing was deleted.')
    localStorage.removeItem(key)
  })
}
