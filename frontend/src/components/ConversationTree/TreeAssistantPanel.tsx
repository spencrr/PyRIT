import { useEffect, useRef, useState } from 'react'
import type { ChangeEvent, FormEvent } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, MessageBar, MessageBarBody, Spinner, Text, Textarea,
} from '@fluentui/react-components'
import type { TextareaOnChangeData } from '@fluentui/react-components'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import { treeAssistantApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type {
  TreeAssistantContext, TreeAssistantProposal, TreeAssistantReceipt, TreeAssistantSession,
  TreeAssistantTurn, TreeWorkspace,
} from '@/types'

import { createAssistantContext, previewAssistantProposal } from './treeAssistant'
import { useTreeAssistantPanelStyles } from './TreeAssistantPanel.styles'

interface TreeAssistantPanelProps {
  readonly workspace: TreeWorkspace
  readonly selectedId: string | null
  readonly active: boolean
  readonly disabled: boolean
  readonly onApply: (proposal: TreeAssistantProposal) => Promise<TreeAssistantReceipt>
  readonly onBusyChange?: (busy: boolean) => void
}

interface MessageRequest {
  readonly request_id: string
  readonly message: string
  readonly context: TreeAssistantContext
}

interface UnreportedResult {
  readonly proposalId: string
  readonly receipt: TreeAssistantReceipt
  readonly error: string
}

interface ProposalCardProps {
  readonly workspace: TreeWorkspace
  readonly proposal: TreeAssistantProposal
  readonly disabled: boolean
  readonly onResolve: (proposal: TreeAssistantProposal, approve: boolean) => Promise<void>
}

function ProposalCard({ workspace, proposal, disabled, onResolve }: ProposalCardProps) {
  const styles = useTreeAssistantPanelStyles()
  const pending = proposal.status === 'pending'
  let preview: ReturnType<typeof previewAssistantProposal> | undefined
  let validationError = ''
  if (pending) {
    try {
      preview = previewAssistantProposal(workspace, proposal)
    } catch (error: unknown) {
      validationError = toApiError(error).detail
    }
  }
  const approvalLabel = proposal.action?.kind === 'run' ? 'Approve run'
    : proposal.action?.kind === 'score' ? 'Approve scoring' : 'Approve edits'
  return (
    <section className={styles.proposal} aria-label="Assistant proposal">
      <div className={styles.proposalBody} role="group" aria-label="Proposal preview" tabIndex={0}>
        <Text weight="semibold">{proposal.summary}</Text>
        {preview && <>
          <Text>{preview.description}</Text>
          <Text className={styles.muted}>
            {preview.operations} estimated target / converter / scorer operations.
          </Text>
        </>}
        {proposal.action.kind === 'mutate' && <ul className={styles.changes}>
          {proposal.action.commands.map((command, index) => <li key={index}>
            <Text weight="semibold">{command.type}</Text>
            {'nodeId' in command && <Text> · {workspace.nodes.find((node) => node.id === command.nodeId)?.prompt.slice(0, 100) ?? command.nodeId}</Text>}
            {'prompt' in command && <Text className={styles.userText}>{command.prompt.slice(0, 600)}{command.prompt.length > 600 ? '… (full prompt in JSON)' : ''}</Text>}
            {command.type === 'sample' && <Text> · {command.count} additional drafts</Text>}
            {command.type === 'childVariants' && <Text> · {command.variants.length} child drafts</Text>}
            {command.type === 'retry' && <Text> · archives the current attempt and resets descendants; no execution</Text>}
          </li>)}
        </ul>}
        <details>
          <summary className={styles.disclosure}>Action details (JSON)</summary>
          <pre className={styles.json} role="region" aria-label="Proposed action JSON" tabIndex={0}>
            {JSON.stringify(proposal.action, null, 2)}
          </pre>
        </details>
        {validationError && <MessageBar intent="warning" layout="multiline">
          <MessageBarBody>{validationError}</MessageBarBody>
        </MessageBar>}
        {!pending && <div role="status" className={styles.message}>
          <Text weight="semibold">
            {proposal.status === 'applied' ? 'Applied' : proposal.status === 'rejected' ? 'Rejected' : 'Failed'}
          </Text>
          {proposal.result && <Text>{proposal.result.detail}</Text>}
        </div>}
      </div>
      {pending && <div className={styles.actions}>
        <Button className={styles.button} appearance="primary" disabled={disabled || !preview}
          onClick={() => { void onResolve(proposal, true) }}>{approvalLabel}</Button>
        <Button className={styles.button} disabled={disabled}
          onClick={() => { void onResolve(proposal, false) }}>Reject</Button>
      </div>}
    </section>
  )
}

function mergeTurns(current: TreeAssistantTurn[], incoming: TreeAssistantTurn[]): TreeAssistantTurn[] {
  const turns = new Map(current.map((turn: TreeAssistantTurn) => [turn.request_id, turn]))
  for (const turn of incoming) {
    const previous = turns.get(turn.request_id)
    turns.set(turn.request_id, {
      ...turn,
      proposals: turn.proposals.map((proposal: TreeAssistantProposal) => {
        const local = previous?.proposals.find((item: TreeAssistantProposal) => item.id === proposal.id)
        return local?.result ? local : proposal
      }),
    })
  }
  return [...turns.values()]
}

export default function TreeAssistantPanel({
  workspace, selectedId, active, disabled, onApply, onBusyChange,
}: TreeAssistantPanelProps) {
  const styles = useTreeAssistantPanelStyles()
  const [session, setSession] = useState<TreeAssistantSession | null>(null)
  const [draft, setDraft] = useState('')
  const [pendingMessage, setPendingMessage] = useState<MessageRequest | null>(null)
  const [messageError, setMessageError] = useState('')
  const [unreported, setUnreported] = useState<UnreportedResult | null>(null)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [discardReport, setDiscardReport] = useState(false)
  const mounted = useRef(false)
  const busyRef = useRef(false)
  const unreportedRef = useRef<UnreportedResult | null>(null)
  const resolvedProposals = useRef(new Set<string>())

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  function begin(): boolean {
    if (!mounted.current || busyRef.current || !active || disabled) return false
    busyRef.current = true
    setBusy(true)
    setError('')
    onBusyChange?.(true)
    return true
  }

  function finish(): void {
    busyRef.current = false
    if (!mounted.current) return
    setBusy(false)
    // Keep the workspace mounted until its execution receipt is acknowledged.
    onBusyChange?.(unreportedRef.current !== null)
  }

  async function startSession(): Promise<void> {
    if (unreportedRef.current || !begin()) return
    try {
      if (session) {
        try {
          await treeAssistantApi.deleteSession(session.session_id)
        } catch (failure: unknown) {
          if (toApiError(failure).status !== 404) throw failure
        }
        if (!mounted.current) return
        setSession(null)
        setDraft('')
        setPendingMessage(null)
        setMessageError('')
        resolvedProposals.current.clear()
      }
      const created = await treeAssistantApi.createSession(workspace.id)
      if (!mounted.current) return
      if (created.workspace_id !== workspace.id) throw new Error('The assistant session belongs to another workspace.')
      setSession(created)
    } catch (failure: unknown) {
      if (mounted.current) setError(toApiError(failure).detail)
    } finally {
      finish()
    }
  }

  async function requestReply(sessionId: string, request: MessageRequest): Promise<void> {
    setMessageError('')
    try {
      const turn = await treeAssistantApi.sendMessage(sessionId, request)
      if (!mounted.current) return
      if (turn.request_id !== request.request_id || turn.proposals.length > 1) {
        throw new Error('The assistant returned an unexpected turn. Check for the reply before continuing.')
      }
      setSession((current: TreeAssistantSession | null) => current
        ? { ...current, turns: mergeTurns(current.turns, [turn]) } : current)
      setPendingMessage(null)
    } catch (failure: unknown) {
      if (mounted.current) setMessageError(toApiError(failure).detail)
    }
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault()
    if (!session || pendingMessage || unreportedRef.current || !draft.trim() || !begin()) return
    try {
      const context: TreeAssistantContext = JSON.parse(JSON.stringify(createAssistantContext(workspace, selectedId)))
      const request: MessageRequest = { request_id: crypto.randomUUID(), message: draft.trim(), context }
      setPendingMessage(request)
      setDraft('')
      await requestReply(session.session_id, request)
    } catch (failure: unknown) {
      if (mounted.current) setError(toApiError(failure).detail)
    } finally {
      finish()
    }
  }

  async function retryMessage(): Promise<void> {
    if (!session || !pendingMessage || unreportedRef.current || !begin()) return
    try {
      await requestReply(session.session_id, pendingMessage)
    } finally {
      finish()
    }
  }

  async function checkForReply(): Promise<void> {
    if (!session || !pendingMessage || unreportedRef.current || !begin()) return
    try {
      const refreshed = await treeAssistantApi.getSession(session.session_id)
      if (!mounted.current) return
      if (refreshed.session_id !== session.session_id || refreshed.workspace_id !== workspace.id
        || refreshed.turns.some((turn: TreeAssistantTurn) => turn.proposals.length > 1)) {
        throw new Error('The assistant returned an unexpected session.')
      }
      setSession((current: TreeAssistantSession | null) => current
        ? { ...current, turns: mergeTurns(current.turns, refreshed.turns) } : current)
      if (refreshed.turns.some((turn: TreeAssistantTurn) => turn.request_id === pendingMessage.request_id)) {
        setPendingMessage(null)
        setMessageError('')
      } else {
        setMessageError('No reply is available yet. Retry the same message or reset the session.')
      }
    } catch (failure: unknown) {
      if (mounted.current) setMessageError(toApiError(failure).detail)
    } finally {
      finish()
    }
  }

  async function reportResult(sessionId: string, result: UnreportedResult): Promise<void> {
    try {
      const acknowledged = await treeAssistantApi.recordResult(sessionId, result.proposalId, result.receipt)
      if (!mounted.current) return
      if (acknowledged.id !== result.proposalId || acknowledged.status !== result.receipt.status
        || acknowledged.result?.status !== result.receipt.status
        || acknowledged.result.revision !== result.receipt.revision || acknowledged.result.detail !== result.receipt.detail) {
        throw new Error('The server did not acknowledge this result. The approved action will not be repeated.')
      }
      unreportedRef.current = null
      setUnreported(null)
    } catch (failure: unknown) {
      if (!mounted.current) return
      const failed = { ...result, error: toApiError(failure).detail }
      unreportedRef.current = failed
      setUnreported(failed)
    }
  }

  async function resolveProposal(proposal: TreeAssistantProposal, approve: boolean): Promise<void> {
    if (!session || pendingMessage || unreportedRef.current || proposal.status !== 'pending'
      || resolvedProposals.current.has(proposal.id) || !begin()) return
    try {
      if (approve) previewAssistantProposal(workspace, proposal)
      resolvedProposals.current.add(proposal.id)
      let receipt: TreeAssistantReceipt
      if (approve) {
        try {
          receipt = await onApply(proposal)
        } catch (failure: unknown) {
          receipt = { status: 'failed', revision: workspace.revision, detail: toApiError(failure).detail }
        }
      } else {
        receipt = { status: 'rejected', revision: workspace.revision, detail: 'Rejected by user.' }
      }
      if (!mounted.current) return
      const result: UnreportedResult = { proposalId: proposal.id, receipt: { ...receipt }, error: '' }
      unreportedRef.current = result
      setUnreported(result)
      setSession((current: TreeAssistantSession | null) => current ? {
        ...current,
        turns: current.turns.map((turn: TreeAssistantTurn) => ({
          ...turn,
          proposals: turn.proposals.map((item: TreeAssistantProposal) => item.id === proposal.id
            ? { ...item, status: result.receipt.status, result: result.receipt } : item),
        })),
      } : current)
      await reportResult(session.session_id, result)
    } catch (failure: unknown) {
      if (mounted.current) setError(toApiError(failure).detail)
    } finally {
      finish()
    }
  }

  async function retryReporting(): Promise<void> {
    if (!session || !unreportedRef.current || !begin()) return
    try {
      const result = { ...unreportedRef.current, error: '' }
      setUnreported(result)
      await reportResult(session.session_id, result)
    } finally {
      finish()
    }
  }

  function discardPendingReport(): void {
    if (busyRef.current || !active || disabled || !unreportedRef.current) return
    unreportedRef.current = null
    setUnreported(null)
    setDiscardReport(false)
    setSession(null)
    setPendingMessage(null)
    setMessageError('')
    setDraft('')
    setError('The unreported receipt was discarded locally. No tree action was repeated; the old chat will expire on the server.')
    onBusyChange?.(false)
  }

  const unavailable = disabled || !active || busy
  const conversationLocked = unavailable || !!pendingMessage || !!unreported

  return (
    <aside aria-label="Tree assistant" hidden={!active} className={styles.root}>
      <header className={styles.header}>
        <h2 className={styles.title}>Tree assistant</h2>
        <Button className={styles.button} disabled={unavailable || !!unreported}
          onClick={() => { void startSession() }}>{session ? 'Reset session' : 'Start session'}</Button>
      </header>
      <div className={styles.content}>
        <div className={styles.stack}>
          <Text className={styles.muted}>Session-only chat. Tree changes and runs require your approval.</Text>
          {error && <MessageBar intent="error" layout="multiline" role="alert"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
          <ol className={styles.transcript} role="log" aria-label="Assistant conversation" aria-live="polite">
            {session?.turns.map((turn: TreeAssistantTurn) => <li key={turn.request_id} className={styles.stack}>
              <div className={styles.message}>
                <Text weight="semibold">You</Text>
                <Text className={styles.userText}>{turn.message}</Text>
              </div>
              <div className={styles.message}>
                <Text weight="semibold">Assistant</Text>
                <MarkdownContent content={turn.reply} />
              </div>
              {turn.proposals.map((proposal: TreeAssistantProposal) => <ProposalCard key={proposal.id}
                workspace={workspace} proposal={proposal} disabled={conversationLocked} onResolve={resolveProposal} />)}
            </li>)}
            {pendingMessage && !session?.turns.some((turn: TreeAssistantTurn) => turn.request_id === pendingMessage.request_id)
              && <li className={styles.message}>
                <Text weight="semibold">You</Text>
                <Text className={styles.userText}>{pendingMessage.message}</Text>
              </li>}
          </ol>
          {messageError && <div className={styles.stack}>
            <MessageBar intent="error" layout="multiline" role="alert"><MessageBarBody>{messageError}</MessageBarBody></MessageBar>
            <div className={styles.actions}>
              <Button className={styles.button} disabled={unavailable} onClick={() => { void retryMessage() }}>Retry message</Button>
              <Button className={styles.button} disabled={unavailable} onClick={() => { void checkForReply() }}>Check for reply</Button>
            </div>
          </div>}
          {unreported && <div className={styles.stack}>
            {unreported.error ? <>
              <MessageBar intent="error" layout="multiline" role="alert">
                <MessageBarBody>Result not reported. {unreported.error}</MessageBarBody>
              </MessageBar>
              <Button className={styles.button} disabled={unavailable}
                onClick={() => { void retryReporting() }}>Retry reporting result</Button>
              <Button className={styles.button} disabled={unavailable}
                onClick={() => { setDiscardReport(true) }}>Discard unreported receipt</Button>
            </> : <Text role="status">Reporting result…</Text>}
          </div>}
          {busy && <Spinner size="tiny" label="Working…" />}
        </div>
      </div>
      <form className={styles.composer} onSubmit={(event: FormEvent<HTMLFormElement>) => { void sendMessage(event) }}>
        <Field label="Message">
          <Textarea className={styles.input} value={draft} disabled={!session || conversationLocked} rows={3} resize="vertical"
            maxLength={32_000}
            onChange={(_event: ChangeEvent<HTMLTextAreaElement>, data: TextareaOnChangeData) => { setDraft(data.value) }} />
        </Field>
        <Button className={styles.button} type="submit" appearance="primary"
          disabled={!session || conversationLocked || !draft.trim()}>Send message</Button>
      </form>
      {discardReport && <Dialog open={active} onOpenChange={(_, data) => { if (!data.open) setDiscardReport(false) }}>
        <DialogSurface><DialogBody>
          <DialogTitle>Discard unreported receipt?</DialogTitle>
          <DialogContent>
            The tree action has already finished and will not be repeated or undone. This discards only its local
            reporting state and detaches the chat, which may have expired. Copy the result before continuing.
            {unreported && <pre className={styles.json}>{JSON.stringify(unreported.receipt, null, 2)}</pre>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} onClick={() => { setDiscardReport(false) }}>Cancel</Button>
            <Button className={styles.button} disabled={unavailable} onClick={discardPendingReport}>Discard receipt and detach chat</Button>
          </DialogActions>
        </DialogBody></DialogSurface>
      </Dialog>}
    </aside>
  )
}
