import { useEffect, useRef, useState } from 'react'

import { treeAssistantApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  TreeAssistantApply, TreeAssistantCheckpoint, TreeAssistantGrant, TreeAssistantJournal, TreeAssistantOperation,
  TreeAssistantPreparedProposal, TreeAssistantProposal, TreeAssistantReceipt, TreeAssistantSequence,
  TreeAssistantSession, TreeAssistantTurn, TreeWorkspace,
} from '@/types'
import {
  captureAssistantPrecondition, createAssistantContext, prepareAssistantProposal, validateAssistantPrecondition, validatePreparedAssistantProposal,
} from '@/components/ConversationTree/treeAssistant'
import { deleteAssistantCheckpoint, loadAssistantCheckpoint, saveAssistantCheckpoint } from '@/components/ConversationTree/treeAssistantStorage'

interface AssistantSessionOptions {
  workspace: TreeWorkspace
  selectedId: string | null
  active: boolean
  disabled: boolean
  getWorkspace?: () => TreeWorkspace
  onApply: TreeAssistantApply
  onStop?: () => void
  onBusyChange?: (busy: boolean) => void
}

const MAX_RECENT_TURNS = 50

function blank(workspaceId: string): TreeAssistantCheckpoint {
  return { schemaVersion: 1, revision: 0, workspaceId, savedAt: new Date().toISOString(),
    session: null, archivedTurns: [], draft: '', pendingMessage: null, unreported: null, executing: null }
}

function journalOf(checkpoint: TreeAssistantCheckpoint): TreeAssistantJournal {
  if (checkpoint.executing) return { kind: 'execution', execution: checkpoint.executing }
  if (checkpoint.unreported) return { kind: 'receipt', result: checkpoint.unreported }
  if (checkpoint.pendingMessage) return { kind: 'message', pending: checkpoint.pendingMessage }
  return { kind: 'ready' }
}

function mergeSession(local: TreeAssistantSession, server: TreeAssistantSession): TreeAssistantSession {
  const turns = new Map(local.turns.map((turn) => [turn.request_id, turn]))
  for (const turn of server.turns) {
    const old = turns.get(turn.request_id)
    turns.set(turn.request_id, { ...turn, proposals: turn.proposals.map((proposal) => {
      const prior = old?.proposals.find((item) => item.id === proposal.id)
      return prior?.result ? prior : proposal
    }) })
  }
  return { ...server, turns: [...turns.values()] }
}

export function useTreeAssistantSession(options: AssistantSessionOptions) {
  const [initial] = useState(() => {
    try { return { checkpoint: loadAssistantCheckpoint(options.workspace.id) ?? blank(options.workspace.id), error: '' } }
    catch (error) { return { checkpoint: blank(options.workspace.id), error: toApiError(error).detail } }
  })
  const [checkpoint, setCheckpoint] = useState<TreeAssistantCheckpoint>(initial.checkpoint)
  const current = useRef(checkpoint)
  const [storageError, setStorageError] = useState(initial.error)
  const storageBlocked = useRef(!!initial.error)
  const [error, setError] = useState('')
  const [operation, setOperation] = useState<TreeAssistantOperation>('idle')
  const operationRef = useRef<TreeAssistantOperation>('idle')
  const [connection, setConnection] = useState<'offline' | 'online'>('offline')
  const connectionRef = useRef<'offline' | 'online'>('offline')
  const [writerState, setWriterState] = useState<'acquiring' | 'writable' | 'read-only'>('acquiring')
  const [writerError, setWriterError] = useState('')
  const writer = useRef(false)
  const mounted = useRef(false)
  const optionsRef = useRef(options)
  useEffect(() => { optionsRef.current = options }, [options])
  useEffect(() => {
    mounted.current = true
    let closed = false
    let release: (() => void) | undefined
    const acquire = async (): Promise<void> => {
      // StrictMode replays effects before browser lock callbacks can release their grants.
      await Promise.resolve()
      if (closed) return
      if (typeof navigator.locks?.request !== 'function') {
        throw new Error('Safe chat persistence requires Web Locks in a secure browser context. Chat is read-only.')
      }
      await navigator.locks.request(`pyrit:tree-assistant-writer:${options.workspace.id}`, { ifAvailable: true }, async (lock) => {
        if (closed) return
        if (!lock) throw new Error('This workspace chat is open in another tab. Close that tab, then reload to resume here.')
        writer.current = true
        setWriterState('writable')
        await new Promise<void>((resolve) => { release = resolve })
      })
    }
    void acquire().catch((failure: unknown) => {
      if (!closed) { setWriterState('read-only'); setWriterError(toApiError(failure).detail) }
    })
    return () => {
      closed = true
      mounted.current = false
      writer.current = false
      optionsRef.current.onStop?.()
      release?.()
    }
  }, [options.workspace.id])
  useEffect(() => {
    const changed = (event: StorageEvent) => {
      if (event.storageArea === localStorage && (event.key === null || event.key === `pyrit:tree-assistant:v1:${options.workspace.id}`)) {
        storageBlocked.current = true
        setStorageError('Chat changed in another tab. Export local context, then reload before continuing.')
      }
    }
    window.addEventListener('storage', changed)
    return () => { window.removeEventListener('storage', changed) }
  }, [options.workspace.id])

  function persist(patch: Partial<TreeAssistantCheckpoint>): TreeAssistantCheckpoint {
    if (!writer.current) throw new Error('This chat is read-only; no local writer lock is held.')
    const candidate = { ...current.current, ...patch }
    if (candidate.session && candidate.session.turns.length > MAX_RECENT_TURNS) {
      const archived = candidate.session.turns.slice(0, -MAX_RECENT_TURNS)
      candidate.archivedTurns = [...(candidate.archivedTurns ?? []), ...archived]
      candidate.session = { ...candidate.session, turns: candidate.session.turns.slice(-MAX_RECENT_TURNS) }
    }
    if (candidate.preconditions) {
      const requests = new Set(candidate.session?.turns.filter((turn) =>
        turn.proposals.some((proposal) => proposal.status === 'pending')).map((turn) => turn.request_id))
      if (candidate.pendingMessage) requests.add(candidate.pendingMessage.request_id)
      candidate.preconditions = Object.fromEntries(Object.entries(candidate.preconditions).filter(([requestId]) => requests.has(requestId)))
    }
    current.current = candidate
    if (!mounted.current) throw new Error('The assistant view was closed.')
    try {
      const saved = saveAssistantCheckpoint(candidate)
      current.current = saved
      setCheckpoint(saved)
      storageBlocked.current = false
      setStorageError('')
      return saved
    } catch (failure) {
      setCheckpoint(candidate)
      storageBlocked.current = true
      setStorageError(toApiError(failure).detail)
      throw failure
    }
  }

  function connect(value: 'offline' | 'online'): void {
    connectionRef.current = value
    setConnection(value)
  }

  function begin(next: TreeAssistantOperation): boolean {
    const config = optionsRef.current
    if (!mounted.current || !writer.current || operationRef.current !== 'idle' || !config.active || config.disabled || storageBlocked.current) return false
    operationRef.current = next
    setOperation(next)
    setError('')
    config.onBusyChange?.(true)
    return true
  }

  function finish(): void {
    operationRef.current = 'idle'
    if (!mounted.current) return
    setOperation('idle')
    optionsRef.current.onBusyChange?.(!!current.current.unreported || !!current.current.executing || storageBlocked.current)
  }

  async function guarded(next: TreeAssistantOperation, work: () => Promise<void>): Promise<void> {
    if (!begin(next)) return
    try { await work() }
    catch (failure) { if (mounted.current) setError(toApiError(failure).detail) }
    finally { finish() }
  }

  async function start(reset = false, preserveContext = false): Promise<void> {
    await guarded('connecting', async () => {
      const local = current.current
      if (reset && (local.unreported || local.executing)) throw new Error('Review the interrupted action or report its result before resetting.')
      if (local.session && !reset) {
        let resumed: TreeAssistantSession
        let restored = false
        try {
          const remote = await treeAssistantApi.getSession(local.session.session_id)
          if (remote.session_id !== local.session.session_id || remote.workspace_id !== local.workspaceId) throw new Error('Unexpected session identity.')
          resumed = mergeSession(local.session, remote)
        }
        catch (failure) {
          if (toApiError(failure).status !== 404) throw failure
          if (local.executing) throw Object.assign(new Error('Review the interrupted action before restoring an expired backend session.'), { cause: failure })
          resumed = await treeAssistantApi.createSession(local.workspaceId, local.session.turns)
          restored = true
        }
        if (resumed.workspace_id !== local.workspaceId) throw new Error('Restored session belongs to another workspace.')
        const pending = local.pendingMessage && !resumed.turns.some((turn) => turn.request_id === local.pendingMessage?.request_id) ? local.pendingMessage : null
        persist({ session: resumed, pendingMessage: pending, ...(restored ? { unreported: null } : {}) })
        connect('online')
        if (restored) setError('Backend session restored from local conversation history. Old pending proposals require re-planning; no actions were replayed.')
        return
      }
      if (local.session) {
        try { await treeAssistantApi.deleteSession(local.session.session_id) }
        catch (failure) { if (toApiError(failure).status !== 404) throw failure }
        connect('offline')
      }
      const created = preserveContext && local.session
        ? await treeAssistantApi.createSession(local.workspaceId, local.session.turns)
        : await treeAssistantApi.createSession(local.workspaceId)
      if (created.workspace_id !== local.workspaceId) throw new Error('The assistant session belongs to another workspace.')
      persist({
        session: created, archivedTurns: preserveContext ? local.archivedTurns ?? [] : [],
        pendingMessage: preserveContext ? local.pendingMessage : null, unreported: null, executing: null,
        ...(reset && !preserveContext ? { draft: '' } : {}),
      })
      connect('online')
      if (preserveContext) setError('Fresh backend session restored from recent chat. Older turns remain archived locally; no actions were replayed.')
    })
  }

  async function request(message: string, autonomy?: TreeAssistantGrant): Promise<TreeAssistantTurn> {
    const local = current.current
    if (!local.session || connectionRef.current !== 'online' || journalOf(local).kind !== 'ready') throw new Error('Resolve pending chat work before sending.')
    const workspace = optionsRef.current.getWorkspace?.() ?? optionsRef.current.workspace
    const context = createAssistantContext(workspace, autonomy?.root_node_id ?? optionsRef.current.selectedId)
    if (autonomy) context.autonomy = { ...autonomy }
    const pending = { request_id: crypto.randomUUID(), message, context }
    persist({ pendingMessage: pending, draft: '', preconditions: {
      ...local.preconditions, [pending.request_id]: captureAssistantPrecondition(workspace),
    } })
    return await requestPending()
  }

  async function requestPending(): Promise<TreeAssistantTurn> {
    const { session, pendingMessage } = current.current
    if (!session || !pendingMessage) throw new Error('No pending message to recover.')
    const turn = await treeAssistantApi.sendMessage(session.session_id, pendingMessage)
    if (turn.request_id !== pendingMessage.request_id || turn.proposals.length > 1) throw new Error('The assistant returned an unexpected turn.')
    persist({ session: mergeSession(current.current.session ?? session, { ...session, turns: [turn] }), pendingMessage: null })
    return turn
  }

  async function send(): Promise<void> {
    if (connectionRef.current !== 'online' || journalOf(current.current).kind !== 'ready' || !current.current.draft.trim()) return
    await guarded('sending', async () => { await request(current.current.draft.trim()) })
  }

  async function retryMessage(): Promise<void> {
    if (connectionRef.current !== 'online' || journalOf(current.current).kind !== 'message') return
    await guarded('sending', async () => { await requestPending() })
  }

  async function recoverReply(): Promise<void> {
    if (!['ready', 'message'].includes(journalOf(current.current).kind)) return
    await guarded('recovering', async () => {
      const { session, pendingMessage } = current.current
      if (!session) throw new Error('No saved session.')
      const remote = await treeAssistantApi.getSession(session.session_id)
      if (remote.session_id !== session.session_id || remote.workspace_id !== session.workspace_id) throw new Error('Unexpected session identity.')
      const done = remote.turns.some((turn) => turn.request_id === pendingMessage?.request_id)
      persist({ session: mergeSession(session, remote), pendingMessage: done ? null : pendingMessage })
      connect('online')
      if (!done && pendingMessage) setError('No completed reply yet. Retry the same message or reset the session.')
    })
  }

  async function report(): Promise<void> {
    const { session, unreported } = current.current
    if (!session || !unreported || connectionRef.current !== 'online') throw new Error('Resume the session before reporting its saved receipt.')
    if (unreported.error) persist({ unreported: { ...unreported, error: '' } })
    try {
      const acknowledged = await treeAssistantApi.recordResult(session.session_id, unreported.proposalId, unreported.receipt)
      if (acknowledged.id !== unreported.proposalId || acknowledged.status !== unreported.receipt.status
        || acknowledged.result?.status !== unreported.receipt.status
        || acknowledged.result.revision !== unreported.receipt.revision || acknowledged.result.detail !== unreported.receipt.detail) {
        throw new Error('The server did not acknowledge this result. The approved action will not be repeated.')
      }
      persist({ unreported: null })
    } catch (failure) {
      if (!storageBlocked.current) persist({ unreported: { ...unreported, error: toApiError(failure).detail } })
      throw Object.assign(new Error(`Result not reported. ${toApiError(failure).detail}`), { cause: failure })
    }
  }

  function saveReceipt(proposal: TreeAssistantProposal, receipt: TreeAssistantReceipt): void {
    const session = current.current.session
    if (!session) throw new Error('The saved session is missing.')
    persist({
      session: { ...session, turns: session.turns.map((turn) => ({
        ...turn, proposals: turn.proposals.map((item) => item.id === proposal.id ? { ...item, status: receipt.status, result: receipt } : item),
      })) },
      executing: null, unreported: { proposalId: proposal.id, receipt, error: '' },
    })
  }

  async function resolve(
    proposal: TreeAssistantProposal, approve: boolean, autonomy?: TreeAssistantGrant, suppliedReview?: TreeAssistantPreparedProposal,
  ): Promise<TreeAssistantReceipt> {
    const local = current.current
    if (!local.session || local.executing || local.unreported || local.pendingMessage) throw new Error('Resolve pending chat work first.')
    const turn = local.session.turns.find((turn) => turn.proposals.some((item) => item.id === proposal.id))
    const savedProposal = turn?.proposals.find((item) => item.id === proposal.id)
    if (savedProposal?.status !== 'pending') throw new Error('This proposal was already resolved.')
    if (!mounted.current || !writer.current || storageBlocked.current || !optionsRef.current.active || optionsRef.current.disabled) {
      throw new Error('The workspace is no longer available for approval.')
    }
    const workspace = optionsRef.current.getWorkspace?.() ?? optionsRef.current.workspace
    const precondition = turn ? local.preconditions?.[turn.request_id] : undefined
    if (approve) validateAssistantPrecondition(workspace, savedProposal, precondition)
    const review = approve ? suppliedReview ?? prepareAssistantProposal(workspace, savedProposal, precondition) : undefined
    if (review) {
      validatePreparedAssistantProposal(workspace, savedProposal, review)
      if (precondition && JSON.stringify(precondition) !== JSON.stringify(review.precondition)) throw new Error('Approval does not match its captured request.')
    }
    persist({ executing: { proposalId: savedProposal.id, baseRevision: workspace.revision, ...(review ? { precondition: review.precondition } : {}) } })
    let receipt: TreeAssistantReceipt
    if (!approve) receipt = { status: 'rejected', revision: workspace.revision, detail: 'Rejected by user.' }
    else {
      try { receipt = await optionsRef.current.onApply(savedProposal, autonomy, review) }
      catch (failure) {
        receipt = { status: 'failed', revision: (optionsRef.current.getWorkspace?.() ?? workspace).revision, detail: toApiError(failure).detail.slice(0, 2000) }
      }
    }
    saveReceipt(savedProposal, receipt)
    await report()
    return receipt
  }

  async function decide(proposal: TreeAssistantProposal, approve: boolean, review?: TreeAssistantPreparedProposal): Promise<void> {
    if (connectionRef.current !== 'online' || journalOf(current.current).kind !== 'ready') return
    await guarded('deciding', async () => { await resolve(proposal, approve, undefined, review) })
  }

  async function reviewInterrupted(): Promise<void> {
    if (journalOf(current.current).kind !== 'execution') return
    await guarded('reviewing', async () => {
      const { executing, session } = current.current
      const proposal = session?.turns.flatMap((turn) => turn.proposals).find((item) => item.id === executing?.proposalId)
      if (!proposal) throw new Error('Interrupted action record is incomplete.')
      saveReceipt(proposal, { status: 'failed', revision: (optionsRef.current.getWorkspace?.() ?? optionsRef.current.workspace).revision,
        detail: 'Interrupted while executing. Outcome may be partial or unknown; user reviewed backend/tree evidence. No action was replayed.' })
      if (connectionRef.current === 'online') await report()
    })
  }

  async function runSequence(work: (sequence: TreeAssistantSequence) => Promise<void>): Promise<void> {
    if (connectionRef.current !== 'online' || journalOf(current.current).kind !== 'ready') return
    await guarded('sequence', async () => {
      await work({ request, resolve: (proposal: TreeAssistantProposal, grant: TreeAssistantGrant, review: TreeAssistantPreparedProposal) =>
        resolve(proposal, true, grant, review) })
    })
  }

  async function retryReporting(): Promise<void> {
    if (connectionRef.current !== 'online' || journalOf(current.current).kind !== 'receipt') return
    await guarded('reporting', report)
  }

  function editDraft(draft: string): void {
    try { persist({ draft }) } catch { /* The recoverable storage error is already visible. */ }
  }

  async function retrySave(): Promise<void> {
    try { persist({}); optionsRef.current.onBusyChange?.(!!current.current.unreported || !!current.current.executing) }
    catch { /* Preserve the candidate for export when storage still fails. */ }
  }

  function detach(): void {
    if (operationRef.current !== 'idle') return
    try {
      persist({ session: null, archivedTurns: [], pendingMessage: null, unreported: null, executing: null })
      connect('offline')
      optionsRef.current.onBusyChange?.(false)
      setError('Detached locally. No action was repeated or undone; the server chat will expire.')
    } catch { /* Keep failed writes and receipts available for export. */ }
  }

  function clearExportedChat(): void {
    if (operationRef.current !== 'idle' || !writer.current) return
    try {
      deleteAssistantCheckpoint(current.current.workspaceId, current.current.revision)
      current.current = blank(current.current.workspaceId)
      setCheckpoint(current.current)
      storageBlocked.current = false
      setStorageError('')
      connect('offline')
      optionsRef.current.onBusyChange?.(false)
      setError('Local chat cleared after export. Backend chat will expire; no tree actions were repeated or undone.')
    } catch (failure) { setError(toApiError(failure).detail) }
  }

  return { checkpoint, error, storageError, writerError, writable: writerState === 'writable',
    busy: operation !== 'idle', connected: connection === 'online', operation, connection, journal: journalOf(checkpoint),
    start, send, retryMessage, recoverReply, decide, retryReporting, reviewInterrupted,
    runSequence, editDraft, retrySave, detach, clearExportedChat }
}
