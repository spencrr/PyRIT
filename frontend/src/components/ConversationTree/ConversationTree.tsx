import { useEffect, useRef, useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle, Field,
  Link, Menu, MenuItem, MenuList, MenuPopover, MenuTrigger, MessageBar, MessageBarBody, Select, Text,
} from '@fluentui/react-components'
import { BranchForkRegular, MoreHorizontalRegular } from '@fluentui/react-icons'

import { convertersApi, scorersApi, targetsApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  BackendScore, ConverterCatalogEntry, TargetInstance,
  TreeCommand, TreeNode, TreeRunResult, TreeScoreRun, TreeSettings, TreeWorkspace,
} from '@/types'
import { downloadTextFile } from '@/utils/conversationExport'
import { fetchAllPages } from '@/utils/fetchAllPages'
import { useTreeContinuation } from '@/hooks/useTreeContinuation'

import { useConversationTreeStyles } from './ConversationTree.styles'
import TreeCanvas from './TreeCanvas'
import { NODE_SIZES } from './treePresentation'
import TreeNodeEditor from './TreeNodeEditor'
import TreeWorkspaceDialog from './TreeWorkspaceDialog'
import TreeSettingsDialog from './TreeSettingsDialog'
import TreeScoringDialog from './TreeScoringDialog'
import { recoverTreeNode, runTree, TreePersistenceError } from './treeExecution'
import {
  applyTreeCommand, exportTreePlan, getNewRunNodeIds, getRunNodeIds, getTreeSettings, isNodeHidden, TREE_WORKSPACE_LABEL,
} from './treeModel'
import { deleteTreeWorkspace, listTreeWorkspaces, loadTreeWorkspace, saveTreeWorkspace, TREE_STORAGE_PREFIX } from './treeStorage'
import { reverseTreeUndo, type TreeUndoEntry } from './treeUndo'
import { discoverTreeContinuation } from './treeHistory'
import { prepareTreeChange } from './treeActions'
import { captureTreeRunResult, formatTreeRunResult } from './treeRunResult'

interface ConversationTreeProps {
  activeTarget: TargetInstance | null
  labels: Record<string, string>
  active?: boolean
}

interface Proposal {
  kind: 'run' | 'score'
  workspaceId: string
  revision: number
  nodeIds: string[]
  operations: number
}

interface RunRecord {
  id: string
  workspaceId: string
  nodeIds: string[]
  effectiveConcurrency?: number
}

function executionSignature(node: TreeNode): string {
  return JSON.stringify({ ...node, position: undefined, size: undefined, kept: undefined, pruned: undefined, scoreRuns: undefined })
}

function scoreView(score: BackendScore): BackendScore {
  return {
    id: score.id, message_piece_id: score.message_piece_id, scorer_type: score.scorer_type,
    score_type: score.score_type, score_value: score.score_value, status: score.status,
    is_objective_score: score.is_objective_score, score_category: score.score_category,
    score_rationale: score.score_rationale, timestamp: score.timestamp,
  }
}

function subtreeIds(workspace: TreeWorkspace, rootId: string): Set<string> {
  const result = new Set([rootId])
  for (let changed = true; changed;) {
    changed = false
    for (const node of workspace.nodes) if (node.parentId && result.has(node.parentId) && !result.has(node.id)) {
      result.add(node.id); changed = true
    }
  }
  return result
}

export default function ConversationTree({ activeTarget, labels, active = true }: ConversationTreeProps) {
  const styles = useConversationTreeStyles()
  const [initial] = useState(() => {
    try { return { workspaces: listTreeWorkspaces(), error: '' } }
    catch (failure) { return { workspaces: [], error: failure instanceof Error ? failure.message : 'Unable to read saved trees.' } }
  })
  const [workspace, setWorkspace] = useState<TreeWorkspace | null>(initial.workspaces[0] ?? null)
  const [workspaces, setWorkspaces] = useState(initial.workspaces)
  const current = useRef<TreeWorkspace | null>(workspace)
  const [selectedId, setSelectedId] = useState<string | null>(workspace?.nodes[0]?.id ?? null)
  const [targets, setTargets] = useState<TargetInstance[]>([])
  const [catalog, setCatalog] = useState<ConverterCatalogEntry[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(initial.error)
  const [notice, setNotice] = useState('')
  const [dirty, setDirty] = useState(false)
  const dirtyRef = useRef(false)
  const [showPruned, setShowPruned] = useState(false)
  const [layoutVersions, setLayoutVersions] = useState<Record<string, number>>({})
  const [dialog, setDialog] = useState<'new' | 'import' | 'settings' | 'scoring' | 'delete' | 'discard-recovery' | null>(null)
  const [proposal, setProposal] = useState<Proposal | null>(null)
  const [skipConfirm, setSkipConfirm] = useState(false)
  const [run, setRun] = useState<RunRecord | null>(null)
  const running = useRef(false)
  const stopped = useRef(false)
  const mounted = useRef(false)
  const [writes, setWrites] = useState(0)
  const pendingWrites = useRef(0)
  const transactionTail = useRef<Promise<void>>(Promise.resolve())
  const pendingCommands = useRef(new Set<string>())
  const scoringTail = useRef<Promise<void>>(Promise.resolve())
  const [focusedGroupId, setFocusedGroupId] = useState<string | null>(null)
  const [activityOpen, setActivityOpen] = useState(false)
  const [dock, setDock] = useState<'graph' | 'inspect'>('inspect')
  const [outlineOpen, setOutlineOpen] = useState(true)
  const [lastRun, setLastRun] = useState<TreeRunResult | null>(null)
  const [undo, setUndo] = useState<TreeUndoEntry[]>([])
  const [redo, setRedo] = useState<TreeUndoEntry[]>([])
  const [importingHistory, setImportingHistory] = useState(false)
  const importingHistoryRef = useRef(false)
  const [recovery, setRecovery] = useState<TreeWorkspace | null>(null)
  const recoveryRef = useRef<TreeWorkspace | null>(null)
  const [recovering, setRecovering] = useState(false)
  const recoveringRef = useRef(false)
  const backend = useTreeContinuation(workspace, selectedId, active, run !== null || recovering)

  function publish(next: TreeWorkspace): void {
    current.current = next
    if (!mounted.current) return
    setWorkspace(next)
    setWorkspaces((previous) => [next, ...previous.filter((item) => item.id !== next.id)])
  }

  function activateWorkspace(next: TreeWorkspace | null): void {
    if (next) publish(next)
    else { current.current = null; setWorkspace(null) }
    setSelectedId(next?.nodes[0]?.id ?? null)
    setFocusedGroupId(null)
    setUndo([]); setRedo([])
    setNotice(''); setError('')
    dirtyRef.current = false; setDirty(false)
  }

  function mutate(operation: (base: TreeWorkspace) => TreeWorkspace): Promise<TreeWorkspace> {
    const workspaceId = current.current?.id
    pendingWrites.current++
    setWrites(pendingWrites.current)
    const task = transactionTail.current.then(async () => {
      const base = current.current
      if (!base || base.id !== workspaceId) throw new Error('Workspace changed before saving.')
      const candidate = operation(base)
      const saved = await saveTreeWorkspace(candidate)
      publish(saved)
      return saved
    }).finally(() => {
      pendingWrites.current--
      if (mounted.current) setWrites(pendingWrites.current)
    })
    // Keep the serializer usable after a rejected operation; callers still receive that rejection.
    transactionTail.current = task.then(() => undefined, () => undefined)
    return task
  }

  async function commitPreparedChange(
    prepare: (base: TreeWorkspace) => ReturnType<typeof prepareTreeChange>,
  ): Promise<ReturnType<typeof prepareTreeChange>> {
    let change: ReturnType<typeof prepareTreeChange> | undefined
    const saved = await mutate((base) => {
      change = prepare(base)
      return change.workspace
    })
    if (!change) throw new Error('The workspace change was not prepared.')
    const undoEntry = change.undo
    if (undoEntry) setUndo((history) => [...history.slice(-19), undoEntry])
    setRedo([])
    if (change.layoutChanged) setLayoutVersions((versions) => ({ ...versions, [saved.id]: (versions[saved.id] ?? 0) + 1 }))
    setFocusedGroupId((id) => id && !saved.groups?.some((group) => group.id === id) ? null : id)
    if (change.selectionId) setSelectedId(change.selectionId)
    return { ...change, workspace: saved }
  }

  function fail(failure: unknown): void {
    if (failure instanceof TreePersistenceError) {
      recoveryRef.current = failure.workspace
      setRecovery(failure.workspace)
    }
    setError(failure instanceof Error ? failure.message : toApiError(failure).detail)
  }

  useEffect(() => {
    mounted.current = true
    function beforeUnload(event: BeforeUnloadEvent): void {
      if (running.current || dirtyRef.current || recoveryRef.current || pendingWrites.current) {
        event.preventDefault(); event.returnValue = ''
      }
    }
    function changed(event: StorageEvent): void {
      if (event.storageArea !== localStorage || (event.key !== null && !event.key.startsWith(TREE_STORAGE_PREFIX))) return
      stopped.current = true
      setNotice('Storage changed in another tab. Reload before editing; the current request may still finish.')
    }
    window.addEventListener('beforeunload', beforeUnload)
    window.addEventListener('storage', changed)
    return () => {
      mounted.current = false; stopped.current = true
      window.removeEventListener('beforeunload', beforeUnload)
      window.removeEventListener('storage', changed)
    }
  }, [])

  useEffect(() => {
    if (!active) { stopped.current = true; return }
    let cancelled = false
    Promise.all([fetchAllPages((cursor) => targetsApi.listTargets(100, cursor)), convertersApi.listConverterCatalog()])
      .then(([registered, converters]) => { if (!cancelled) { setTargets(registered); setCatalog(converters.items) } })
      .catch((failure: unknown) => { if (!cancelled) setError(toApiError(failure).detail) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true; stopped.current = true }
  }, [active])

  async function scoreNodeNow(node: TreeNode, settings: TreeSettings): Promise<void> {
    if (!node?.attackResultId || !node.conversationId || !node.attemptId) throw new Error('This node has no persisted response to score.')
    const evidence = node.messages?.filter((message) => message.role === 'assistant').slice(-1)[0]
    if (!evidence) throw new Error('No recorded response is available to score.')
    for (const scorer of settings.scorers) {
      if (stopped.current) break
      let result: TreeScoreRun
      try {
        const response = await scorersApi.score(scorer.scorer_id, {
          attack_result_id: node.attackResultId, conversation_id: node.conversationId,
          expected_scorer_hash: scorer.identifier_hash, objective: settings.objective, scope: scorer.scope,
          evidence_sequence: evidence.turn_number,
          evidence_message_piece_ids: evidence.message_pieces.map((piece) => piece.id),
          expected_response: evidence.message_pieces.map((piece) => ({
            id: piece.id, converted_value: piece.converted_value, converted_value_data_type: piece.converted_value_data_type,
          })),
        })
        if (response.scorer_hash !== scorer.identifier_hash || response.scorer_id !== scorer.scorer_id) throw new Error('Scorer identity changed.')
        result = { id: crypto.randomUUID(), scorerId: scorer.scorer_id, scorerHash: scorer.identifier_hash, status: response.status, scores: response.scores.map(scoreView) }
      } catch (failure) {
        result = { id: crypto.randomUUID(), scorerId: scorer.scorer_id, scorerHash: scorer.identifier_hash,
          status: 'error', scores: [], error: toApiError(failure).detail }
      }
      const command: TreeCommand = { type: 'score', nodeId: node.id, attemptId: node.attemptId, result }
      try { await mutate((base) => applyTreeCommand(base, command)) }
      catch (failure) {
        const base = current.current
        if (base) throw new TreePersistenceError(applyTreeCommand(base, command), failure)
        throw failure
      }
    }
  }

  function scoreNode(nodeId: string, settings: TreeSettings): Promise<void> {
    const node = current.current?.nodes.find((item) => item.id === nodeId)
    if (!node) return Promise.reject(new Error('The selected response no longer exists.'))
    const task = scoringTail.current.then(() => scoreNodeNow(node, settings))
    scoringTail.current = task.then(() => undefined, () => undefined)
    return task
  }

  function planned(snapshot: TreeWorkspace, nodeIds: string[], kind: Proposal['kind']): Proposal {
    const settings = getTreeSettings(snapshot)
    const operations = snapshot.nodes.filter((node) => nodeIds.includes(node.id))
      .reduce((sum, node) => sum + (kind === 'score' ? settings.scorers.length : 1 + node.converters.length + (settings.autoScore ? settings.scorers.length : 0)), 0)
    if (operations > settings.operationBudget) throw new Error(`This run needs ${operations} operations; workspace limit is ${settings.operationBudget}. Change it in Workspace settings.`)
    return { kind, workspaceId: snapshot.id, revision: snapshot.revision, nodeIds, operations }
  }

  async function execute(approved: Proposal): Promise<TreeRunResult | null> {
    if (running.current || recoveryRef.current || !current.current) {
      setError('Execution is busy or requires recovery.')
      return null
    }
    const snapshot = current.current
    if (snapshot.id !== approved.workspaceId || snapshot.revision !== approved.revision) {
      setError('Workspace changed. Review the run again.')
      return null
    }
    running.current = true; stopped.current = false
    const record = { id: crypto.randomUUID(), workspaceId: snapshot.id, nodeIds: approved.nodeIds }
    setRun(record)
    setError(''); setNotice('')
    const settings = getTreeSettings(snapshot)
    let effectiveConcurrency: number | undefined
    let executionError: string | undefined
    try {
      if (approved.kind === 'score') {
        for (const nodeId of approved.nodeIds) {
          if (stopped.current) break
          await scoreNode(nodeId, settings)
        }
      } else {
        await runTree(snapshot, {
          nodeIds: approved.nodeIds, save: saveTreeWorkspace, onUpdate: publish,
          getLatest: () => current.current ?? snapshot,
          isStopped: () => stopped.current,
          onConcurrencyResolved: (concurrency: number) => {
            effectiveConcurrency = concurrency
            setRun((record) => record ? { ...record, effectiveConcurrency: concurrency } : record)
            if (concurrency < (settings.concurrency ?? 1)) setNotice('This target has not been verified for shared parallel requests; this run uses one request at a time.')
          },
          onPersistenceFailure: (candidate: TreeWorkspace) => {
            recoveryRef.current = candidate
            setRecovery(candidate)
            setNotice('A save failed. Settling in-flight responses before recovery.')
          },
          commitNodeUpdate: (nodeId: string, expected: TreeNode, update: Partial<TreeNode>) => mutate((base) => {
            const node = base.nodes.find((item) => item.id === nodeId)
            if (!node || executionSignature(node) !== executionSignature(expected)) throw new Error('Attempt changed before execution update; stopped without overwriting edits.')
            return { ...base, nodes: base.nodes.map((item) => item.id === nodeId ? { ...item, ...update } : item) }
          }),
          onNodeCompleted: async (_snapshot: TreeWorkspace, nodeId: string) => { if (settings.autoScore) await scoreNode(nodeId, settings) },
        })
      }
    } catch (failure) {
      if (mounted.current) fail(failure)
      executionError = toApiError(failure).detail
    }
    finally { running.current = false; if (mounted.current) setRun(null) }
    const result = captureTreeRunResult({
      id: record.id, before: snapshot, after: recoveryRef.current ?? current.current ?? snapshot,
      nodeIds: approved.nodeIds, kind: approved.kind, stopped: stopped.current,
      error: executionError, persisted: recoveryRef.current === null, effectiveConcurrency,
    })
    if (mounted.current) {
      setLastRun(result)
      setNotice(formatTreeRunResult(result))
    }
    return result
  }

  function requestRun(snapshot: TreeWorkspace, ids: string[], kind: Proposal['kind'] = 'run', automatic = false): void {
    if (!ids.length) { setNotice('No eligible nodes in this selection.'); return }
    try {
      const next = planned(snapshot, ids, kind)
      if (automatic || !getTreeSettings(snapshot).confirmRuns) void execute(next)
      else { setSkipConfirm(false); setProposal(next) }
    } catch (failure) { fail(failure) }
  }

  async function commitCommand(command: TreeCommand): Promise<boolean> {
    if (!current.current || recoveryRef.current) return false
    const commandKey = JSON.stringify(command)
    if (pendingCommands.current.has(commandKey)) return false
    pendingCommands.current.add(commandKey)
    if (!running.current && active) stopped.current = false
    setError('')
    try {
      const change = await commitPreparedChange((base) => prepareTreeChange(base, [command]))
      const saved = change.workspace
      const added = change.addedNodeIds
      if (command.type === 'edit' || command.type === 'retry' || added.length) { dirtyRef.current = false; setDirty(false) }
      if (run && command.type === 'edit') {
        const affected = subtreeIds(saved, command.nodeId)
        setRun({ ...run, nodeIds: run.nodeIds.filter((id) => !affected.has(id)) })
        setNotice('Edited branch removed from the current queue. Run it again when ready.')
      }
      if (!running.current && !stopped.current && active) {
        if (command.type === 'retry') {
          const ids = command.scope === 'node' ? [command.nodeId] : getRunNodeIds(saved, command.nodeId)
          requestRun(saved, ids, 'run')
        } else if (added.length && command.type !== 'importContinuation' && getTreeSettings(saved).autoRun) {
          try { requestRun(saved, getNewRunNodeIds(saved, added), 'run', true) }
          catch (failure) { fail(failure); setNotice('Branches saved. Run their parent first.') }
        }
      }
      return true
    } catch (failure) { fail(failure); return false }
    finally { pendingCommands.current.delete(commandKey) }
  }

  async function undoEdit(forward: boolean): Promise<void> {
    const history = forward ? redo : undo
    const entry = history[history.length - 1]
    if (!entry || dirtyRef.current || running.current || pendingWrites.current || recoveryRef.current) return
    try {
      await mutate((base) => reverseTreeUndo(base, entry, forward))
      if (forward) { setRedo(history.slice(0, -1)); setUndo((items) => [...items, entry]) }
      else { setUndo(history.slice(0, -1)); setRedo((items) => [...items, entry]) }
    } catch (failure) { fail(failure) }
  }

  async function importHistory(nodeId: string): Promise<void> {
    const snapshot = current.current
    if (!snapshot || importingHistoryRef.current || running.current || dirtyRef.current || pendingWrites.current || recoveryRef.current) return
    importingHistoryRef.current = true
    setImportingHistory(true)
    try {
      const continuation = await discoverTreeContinuation(snapshot, nodeId)
      if (continuation.nodes.length) await commitCommand({ type: 'importContinuation', nodeId, nodes: continuation.nodes })
      else setNotice('No new complete exchanges to import.')
      backend.refresh()
    } catch (failure) { fail(failure) }
    finally { importingHistoryRef.current = false; setImportingHistory(false) }
  }

  async function selectNode(id: string): Promise<void> {
    if (dirtyRef.current && id !== selectedId) { setNotice('Apply or discard edits before selecting another node.'); return }
    const base = current.current
    if (!base) return
    for (const group of base.groups ?? []) {
      const member = group.nodeIds.find((rootId) => subtreeIds(base, rootId).has(id))
      if (group.collapsed && member && member !== group.activeNodeId) await commitCommand({ type: 'group', groupId: group.id, activeNodeId: member })
    }
    setSelectedId(id)
  }

  async function recover(nodeId?: string): Promise<void> {
    if (running.current || recoveringRef.current || !current.current) return
    recoveringRef.current = true
    setRecovering(true)
    const snapshot = current.current
    const expected = snapshot.nodes.find((node) => node.id === nodeId)
    try {
      const saved = recoveryRef.current
        ? await saveTreeWorkspace(recoveryRef.current)
        : await recoverTreeNode(snapshot, nodeId ?? '', (candidate) => mutate((base) => {
          const currentNode = base.nodes.find((node) => node.id === nodeId)
          const restored = candidate.nodes.find((node) => node.id === nodeId)
          if (!expected || !currentNode || !restored || executionSignature(currentNode) !== executionSignature(expected)) {
            throw new Error('Attempt changed while recovering. Reload its recorded result.')
          }
          return { ...base, nodes: base.nodes.map((node) => node.id === nodeId ? {
            ...restored, position: node.position, size: node.size, pruned: node.pruned, kept: node.kept, scoreRuns: node.scoreRuns,
          } : node) }
        }))
      publish(saved); recoveryRef.current = null; setRecovery(null); setError('')
      setNotice('Recovered recorded evidence without resending.')
    } catch (failure) { fail(failure) }
    finally { recoveringRef.current = false; if (mounted.current) setRecovering(false) }
  }

  function openWorkspace(id: string): void {
    try {
      const loaded = loadTreeWorkspace(id)
      activateWorkspace(loaded)
    } catch (failure) { fail(failure) }
  }

  function download(plan: boolean, source: TreeWorkspace | null = workspace): void {
    if (!source) return
    try { downloadTextFile(plan ? exportTreePlan(source) : JSON.stringify(source, null, 2), `tree-${source.id}-${plan ? 'plan' : 'evidence'}.json`, 'application/json') }
    catch (failure) { fail(failure) }
  }

  const selected = (recovery ?? workspace)?.nodes.find((node) => node.id === selectedId)
  const settings = workspace ? getTreeSettings(workspace) : null
  const locked = recovery !== null || recovering || writes > 0 || importingHistory
  const viewLocked = locked || run !== null || dirty
  const selectedLocked = recovery !== null || recovering || importingHistory
  const group = workspace?.groups?.find((entry) => selected && entry.nodeIds.includes(selected.id))
  const visible = workspace?.nodes.filter((node) => showPruned || !isNodeHidden(workspace, node.id)) ?? []
  const continuation = backend.continuation
  const activity = lastRun?.workspaceId === workspace?.id ? lastRun : null

  function askScore(rootId?: string, subtree = false): void {
    if (!workspace || !settings) return
    const included = rootId && subtree ? subtreeIds(workspace, rootId) : undefined
    const ids = workspace.nodes.filter((node) => !isNodeHidden(workspace, node.id) && node.conversationId
      && (node.status === 'completed' || node.status === 'error')
      && node.messages?.some((message) => message.role === 'assistant')
      && (!rootId || included?.has(node.id) || node.id === rootId)).map((node) => node.id)
    requestRun(workspace, ids, 'score')
  }

  return (
    <div className={styles.root}>
      <header className={styles.toolbar}>
        <BranchForkRegular fontSize={24} /><Text as="h1" className={styles.title}>Conversation tree</Text>
        {workspace && <Select aria-label="Workspace" className={styles.workspaceSelect} value={workspace.id} disabled={viewLocked}
          onChange={(_, data) => { openWorkspace(data.value) }}>
          {workspaces.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </Select>}
        <Menu><MenuTrigger disableButtonEnhancement><Button className={styles.button} icon={<MoreHorizontalRegular />} aria-label="Workspace options" /></MenuTrigger>
          <MenuPopover><MenuList>
            <MenuItem disabled={viewLocked || loading} onClick={() => { setDialog('new') }}>New tree</MenuItem>
            <MenuItem disabled={viewLocked || loading} onClick={() => { setDialog('import') }}>Import plan</MenuItem>
            <MenuItem disabled={!workspace || viewLocked} onClick={() => { download(true) }}>Export plan</MenuItem>
            <MenuItem disabled={!workspace || viewLocked} onClick={() => { download(false) }}>Export evidence</MenuItem>
            <MenuItem disabled={!workspace} onClick={() => { setDialog('settings') }}>Workspace settings</MenuItem>
            <MenuItem disabled={!workspace} onClick={() => { setDialog('scoring') }}>Scoring</MenuItem>
            <MenuItem disabled={!workspace || locked} onClick={() => { void commitCommand({ type: 'autoLayout' }) }}>Auto layout</MenuItem>
            <MenuItem disabled={!workspace} onClick={() => { setShowPruned(!showPruned) }}>{showPruned ? 'Hide pruned' : 'Show pruned'}</MenuItem>
            <MenuItem disabled={!workspace || viewLocked || !undo.length} onClick={() => { void undoEdit(false) }}>Undo edit</MenuItem>
            <MenuItem disabled={!workspace || viewLocked || !redo.length} onClick={() => { void undoEdit(true) }}>Redo edit</MenuItem>
            <MenuItem disabled={!workspace || viewLocked || !settings?.scorers.length} onClick={() => { askScore() }}>Score existing responses</MenuItem>
            <MenuItem disabled={!workspace || viewLocked} onClick={() => { if (workspace) openWorkspace(workspace.id) }}>Reload saved</MenuItem>
            <MenuItem disabled={!workspace || viewLocked} onClick={() => { setDialog('delete') }}>Delete local tree</MenuItem>
          </MenuList></MenuPopover>
        </Menu>
        <div className={styles.spacer} />
        {workspace && settings && <>
          <Button className={styles.button} aria-pressed={outlineOpen} onClick={() => { setOutlineOpen(!outlineOpen) }}>Branches</Button>
          <Button className={styles.mobileOnly} aria-pressed={dock === 'graph'} onClick={() => { setDock('graph') }}>Graph</Button>
          <Button className={styles.button} aria-pressed={dock === 'inspect'} onClick={() => { setDock('inspect') }}>Inspect</Button>
          <Button className={styles.button} aria-pressed={activityOpen} onClick={() => { setActivityOpen(!activityOpen) }}>Activity</Button>
          {run ? <Button className={styles.button} onClick={() => { stopped.current = true }}>Stop after in-flight requests</Button>
            : <Button className={styles.button} appearance="primary" disabled={locked || dirty} onClick={() => {
              try { requestRun(workspace, getRunNodeIds(workspace)) } catch (failure) { fail(failure) }
            }}>{settings.confirmRuns ? 'Review & run drafts' : 'Run drafts'}</Button>}
        </>}
      </header>
      {error && <MessageBar layout="multiline" intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      {notice && <MessageBar role="status" aria-label="Workspace activity" layout="multiline"><MessageBarBody>{notice}</MessageBarBody></MessageBar>}
      {dirty && <MessageBar layout="multiline"><MessageBarBody>Unsaved edits. Current requests continue; apply edits to remove that branch from their queue.</MessageBarBody></MessageBar>}
      {recovery && <MessageBar layout="multiline" intent="warning"><MessageBarBody>
        Unsaved execution evidence retained. Save or export before leaving; a revision conflict will not overwrite another tab.
        <Button className={styles.button} disabled={recovering || !!run} onClick={() => { void recover() }}>Retry saving only</Button>
        <Button className={styles.button} onClick={() => { download(false, recovery) }}>Export unsaved snapshot</Button>
        <Button className={styles.button} disabled={!!run} onClick={() => { setDialog('discard-recovery') }}>Discard unsaved snapshot</Button>
      </MessageBarBody></MessageBar>}
      {workspace && settings ? <>
        <div className={styles.workspaceStatus}>
          <Text className={styles.muted}>{settings.traversal === 'breadth-first' ? 'BFS' : 'DFS'} · Up to {settings.concurrency ?? 1} at a time · Budget {settings.operationBudget} · {settings.confirmRuns ? 'Ask before runs' : 'Confirmations off'}{settings.autoRun ? ' · Auto-run' : ''}</Text>
        </div>
        {focusedGroupId && <div className={styles.groupHeader}>
          <Button className={styles.button} onClick={() => { setFocusedGroupId(null) }}>Back to workspace</Button>
          <Text>Group comparison · unrelated branches hidden, not pruned</Text>
        </div>}
        {activityOpen && <section aria-label="Run activity" className={styles.toolbar}>
          {run ? <>
            <Text>Running: {run.nodeIds.length} planned nodes</Text>
            {run.effectiveConcurrency !== undefined && <Text>Concurrency: {run.effectiveConcurrency}</Text>}
            {['running', 'completed', 'error', 'draft'].map((status) => <Text key={status}>
              {status === 'draft' ? 'Queued / blocked' : status}: {workspace.nodes.filter((node) => run.nodeIds.includes(node.id) && node.status === status).length}
            </Text>)}
          </> : activity ? <>
            <Text>Last run: {formatTreeRunResult(activity)}</Text>
            {activity.effectiveConcurrency !== undefined && <Text>Concurrency: {activity.effectiveConcurrency}</Text>}
            <Text className={styles.muted}>Recorded at revision {activity.revision}. Later edits and retries do not change this outcome.</Text>
          </> : <Text>No run in this session.</Text>}
        </section>}
        <div className={styles.body} data-dock={dock}>
          <aside hidden={!outlineOpen} className={styles.outline} aria-label="Tree outline"><div className={styles.stack}>
            <Text weight="semibold">Branches</Text>
            {visible.map((node) => <Button key={node.id} className={styles.outlineButton}
              appearance={selectedId === node.id ? 'primary' : 'subtle'} aria-pressed={selectedId === node.id}
              onClick={() => {
                if (!dirtyRef.current) setFocusedGroupId(null)
                void selectNode(node.id)
              }}>{node.prompt.slice(0, 55)} ({node.status}){isNodeHidden(workspace, node.id) ? ' · Pruned' : node.kept ? ' · Kept' : ''}</Button>)}
            <Text className={styles.muted}>Graph saved in this browser. Responses and scores persist in backend history.</Text>
            <Link href={`/history/attacks?${new URLSearchParams({ label: `${TREE_WORKSPACE_LABEL}:${workspace.id}` })}`}>Workspace history</Link>
          </div></aside>
          {active && <TreeCanvas workspace={workspace} key={workspace.id} selectedId={selectedId} showPruned={showPruned}
            disabled={recovery !== null || recovering} layoutVersion={layoutVersions[workspace.id] ?? 0} queuedIds={run?.nodeIds}
            focusedGroupId={focusedGroupId} onFocusGroup={setFocusedGroupId}
            onSelect={(id) => { void selectNode(id) }} onMove={(nodeId, position) => { void commitCommand({ type: 'move', nodeId, position }) }}
            onGroup={(command) => { void commitCommand(command) }} />}
          <aside className={styles.inspector} aria-label="Turn inspector">
            {group && <div className={styles.row}>
              <Button className={styles.button} onClick={() => { setFocusedGroupId(group.id) }}>Focus group ({group.nodeIds.length})</Button>
              <Button className={styles.button} disabled={locked} onClick={() => { void commitCommand({ type: 'group', groupId: group.id, collapsed: !group.collapsed }) }}>
                {group.collapsed ? 'Expand & auto layout' : 'Stack group'} ({group.nodeIds.length})
              </Button>
            </div>}
            {selected && <Field label="Node size">
              <Select disabled={selectedLocked} value={selected.size ? 'custom' : 'default'} onChange={(_, data) => {
                const key = data.value
                if (key === 'default') void commitCommand({ type: 'resize', nodeId: selected.id })
                else if (key === 'compact' || key === 'standard' || key === 'expanded') void commitCommand({ type: 'resize', nodeId: selected.id, size: { ...NODE_SIZES[key] } })
              }}>
                <option value="default">Workspace default</option><option value="compact">Compact</option><option value="standard">Standard</option><option value="expanded">Expanded preview</option>
                {selected.size && <option value="custom">Custom ({Math.round(selected.size.width)} × {Math.round(selected.size.height)})</option>}
              </Select>
            </Field>}
            {selected?.conversationId && <div className={styles.stack}>
              <Button className={styles.button} disabled={!!run || locked} onClick={backend.refresh}>Check backend history</Button>
              {backend.error && <MessageBar layout="multiline" intent="warning"><MessageBarBody>{backend.error}</MessageBarBody></MessageBar>}
              {continuation && (continuation.nodes.length > 0 || continuation.pendingMessages > 0) && <MessageBar layout="multiline"><MessageBarBody>
                {continuation.nodes.length} new complete turns; {continuation.pendingMessages} pending messages.
                {!!continuation.nodes.length && <details><summary>Preview continuation</summary>
                  <ol>{continuation.nodes.map((node) => <li key={node.id}>{node.prompt.slice(0, 240)}</li>)}</ol>
                </details>}
                <Button className={styles.button} disabled={viewLocked || !continuation.nodes.length}
                  onClick={() => { void importHistory(continuation.nodeId) }}>Import continuation</Button>
              </MessageBarBody></MessageBar>}
            </div>}
            {selected && <TreeNodeEditor key={`${selected.id}:${selected.attemptId}:${selected.prompt}:${JSON.stringify(selected.converters)}`}
              node={selected} hidden={isNodeHidden(workspace, selected.id)} catalog={catalog} targets={targets}
              disabled={selectedLocked} hasChildren={workspace.nodes.some((node) => node.parentId === selected.id)}
              active={active} autoRun={settings.autoRun} canRun={!run && !locked} settings={settings}
              onCommand={commitCommand} onRecover={(id) => { void recover(id) }}
              onRun={(id) => { try { requestRun(workspace, getRunNodeIds(workspace, id)) } catch (failure) { fail(failure) } }}
              onScore={askScore} onMarkdownChange={(markdown) => { void commitCommand({ type: 'settings', settings: { ...settings, markdown } }) }}
              onDirtyChange={(value) => { dirtyRef.current = value; setDirty(value) }} />}
          </aside>
        </div>
      </> : <section className={styles.empty}>
        <Text as="h2" size={700}>Explore conversations, not just prompts.</Text>
        <Text>Compare branches, retain attempts, and evaluate responses.</Text>
        <Button className={styles.button} appearance="primary" disabled={loading} onClick={() => { setDialog('new') }}>Create your first tree</Button>
      </section>}
      {(dialog === 'new' || dialog === 'import') && <TreeWorkspaceDialog targets={targets} activeTarget={activeTarget} labels={labels}
        importing={dialog === 'import'} open={active} onClose={() => { setDialog(null) }} onCreate={async (next) => {
          const saved = await saveTreeWorkspace(next); activateWorkspace(saved); stopped.current = false
        }} />}
      {settings && dialog === 'settings' && <TreeSettingsDialog settings={settings} open={active} onClose={() => { setDialog(null) }}
        onSave={(next) => commitCommand({ type: 'settings', settings: next })} />}
      {settings && dialog === 'scoring' && <TreeScoringDialog settings={settings} targets={targets} open={active} onClose={() => { setDialog(null) }}
        onSave={(next) => commitCommand({ type: 'settings', settings: next })} />}
      <Dialog open={active && proposal !== null} onOpenChange={(_, data) => { if (!data.open) setProposal(null) }}>
        <DialogSurface><DialogBody><DialogTitle>{proposal?.kind === 'score' ? 'Approve scoring' : 'Approve model calls'}</DialogTitle>
          <DialogContent className={styles.stack}>
            <Text>{proposal?.nodeIds.length} {proposal?.kind === 'score' ? 'responses to score' : 'target sends'} · {proposal?.operations} planned operations</Text>
            <Text>Provider retries, per-message scoring and composite scorers may make additional calls. This is not a monetary budget.</Text>
            <Checkbox label="Don't ask again in this workspace" checked={skipConfirm} onChange={(_, data) => { setSkipConfirm(data.checked === true) }} />
          </DialogContent><DialogActions>
            <Button className={styles.button} onClick={() => { setProposal(null) }}>Cancel</Button>
            <Button className={styles.button} appearance="primary" onClick={() => {
              const approved = proposal; setProposal(null)
              if (!approved) return
              void (async () => {
                if (skipConfirm && current.current) {
                  const saved = await mutate((base) => {
                    if (base.id !== approved.workspaceId || base.revision !== approved.revision) throw new Error('Workspace changed. Review the run again.')
                    return applyTreeCommand(base, { type: 'settings', settings: { ...getTreeSettings(base), confirmRuns: false } })
                  })
                  await execute({ ...approved, revision: saved.revision })
                } else await execute(approved)
              })().catch(fail)
            }}>Run approved drafts</Button>
          </DialogActions></DialogBody></DialogSurface>
      </Dialog>
      <Dialog open={active && (dialog === 'delete' || dialog === 'discard-recovery')} onOpenChange={(_, data) => { if (!data.open) setDialog(null) }}>
        <DialogSurface><DialogBody><DialogTitle>{dialog === 'delete' ? 'Delete local tree?' : 'Discard unsaved snapshot?'}</DialogTitle>
          <DialogContent>Backend evidence is not deleted. Export local work before discarding it.</DialogContent><DialogActions>
            <Button className={styles.button} onClick={() => { setDialog(null) }}>Cancel</Button>
            <Button className={styles.button} onClick={() => {
              void (async () => {
                if (dialog === 'delete' && current.current) {
                  await deleteTreeWorkspace(current.current)
                  const remaining = listTreeWorkspaces(); setWorkspaces(remaining); activateWorkspace(remaining[0] ?? null)
                } else { recoveryRef.current = null; setRecovery(null); if (current.current) openWorkspace(current.current.id) }
                setDialog(null)
              })().catch(fail)
            }}>{dialog === 'delete' ? 'Delete local workspace' : 'Discard and reload saved'}</Button>
          </DialogActions></DialogBody></DialogSurface>
      </Dialog>
    </div>
  )
}
