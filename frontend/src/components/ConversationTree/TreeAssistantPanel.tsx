import { useMemo, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Menu, MenuItem, MenuList, MenuPopover, MenuTrigger, MessageBar, MessageBarBody, Spinner, Text, Textarea,
} from '@fluentui/react-components'
import { MoreHorizontalRegular } from '@fluentui/react-icons'

import MarkdownContent from '@/components/Markdown/MarkdownContent'
import { useTreeAssistantSession } from '@/hooks/useTreeAssistantSession'
import { toApiError } from '@/services/errors'
import type {
  TreeAssistantApply, TreeAssistantNodeChange, TreeAssistantPrecondition, TreeAssistantPreparedProposal,
  TreeAssistantProposal, TreeAssistantTurn, TreeConverterSpec, TreeWorkspace,
} from '@/types'
import { downloadTextFile } from '@/utils/conversationExport'

import { prepareAssistantProposal } from './treeAssistant'
import { exportAssistantChat } from './treeAssistantStorage'
import TreeAssistantTurnDetails from './TreeAssistantTurnDetails'
import { useTreeAssistantPanelStyles } from './TreeAssistantPanel.styles'

interface TreeAssistantPanelProps {
  readonly workspace: TreeWorkspace
  readonly selectedId: string | null
  readonly active: boolean
  readonly disabled: boolean
  readonly onApply: TreeAssistantApply
  readonly getWorkspace?: () => TreeWorkspace
  readonly onStop?: () => void
  readonly onBusyChange?: (busy: boolean) => void
}

interface ProposalCardProps {
  workspace: TreeWorkspace
  proposal: TreeAssistantProposal
  precondition?: TreeAssistantPrecondition
  disabled: boolean
  onResolve: (proposal: TreeAssistantProposal, approve: boolean, review?: TreeAssistantPreparedProposal) => Promise<void>
}

interface NodeChangeProps {
  readonly change: TreeAssistantNodeChange
}

interface ReviewValueProps {
  readonly label: string
  readonly value: string
}

const PROMPT_LABEL_LENGTH = 80
const VALUE_PREVIEW_LENGTH = 160

function promptLabel(prompt: string): string {
  return prompt.length > PROMPT_LABEL_LENGTH ? `${prompt.slice(0, PROMPT_LABEL_LENGTH)}…` : prompt || '(empty prompt)'
}

function ReviewValue({ label, value }: ReviewValueProps) {
  const styles = useTreeAssistantPanelStyles()
  const [open, setOpen] = useState(false)
  if (value.length <= VALUE_PREVIEW_LENGTH) return <span className={styles.userText}>{value}</span>
  return <details open={open} onToggle={(event) => { setOpen(event.currentTarget.open) }}>
    <summary className={styles.disclosure} aria-label={`Show full ${label}`}>{value.slice(0, VALUE_PREVIEW_LENGTH)}… <Text className={styles.muted}>Full {label}</Text></summary>
    {open && <pre className={styles.json} tabIndex={0} aria-label={`Full ${label}`}>{value}</pre>}
  </details>
}

function pipelineText(converters: TreeConverterSpec[]): string {
  return converters.length ? converters.map((converter: TreeConverterSpec, index: number) =>
    `${index + 1}. ${converter.type}${Object.keys(converter.params).length ? ` (${Object.entries(converter.params)
      .map(([key, value]: [string, unknown]) => `${key}: ${typeof value === 'string' ? value : JSON.stringify(value)}`).join(', ')})` : ''}`).join('\n') : 'None'
}

function NodeChange({ change }: NodeChangeProps) {
  const styles = useTreeAssistantPanelStyles()
  const prompt = change.after?.prompt ?? change.before?.prompt ?? ''
  const beforePipeline = change.before ? pipelineText(change.before.converters) : ''
  const afterPipeline = change.after ? pipelineText(change.after.converters) : 'None'
  return <li className={styles.change}>
    <Text weight="semibold">{change.before ? 'Update' : 'Create'}: {promptLabel(prompt)}</Text>
    <dl className={styles.fields}>
      {change.before && change.before.prompt !== prompt && <>
        <dt>Prompt before</dt><dd><ReviewValue label="prompt before" value={change.before.prompt} /></dd>
        <dt>Prompt after</dt><dd><ReviewValue label="prompt after" value={prompt} /></dd>
      </>}
      {!change.before && prompt.length > PROMPT_LABEL_LENGTH && <><dt>Prompt</dt><dd><ReviewValue label="prompt" value={prompt} /></dd></>}
      {change.before && beforePipeline !== afterPipeline && <>
        <dt>Pipeline before</dt><dd><ReviewValue label="pipeline before" value={beforePipeline} /></dd>
      </>}
      {beforePipeline !== afterPipeline && <>
        <dt>{change.before ? 'Pipeline after' : 'Pipeline'}</dt><dd><ReviewValue label="pipeline after" value={afterPipeline} /></dd>
      </>}
      {change.before && change.before.status !== change.after?.status && <><dt>State</dt><dd>{change.before.status} → {change.after?.status ?? 'Removed'}</dd></>}
      {change.beforeHidden !== undefined && change.beforeHidden !== change.afterHidden && <><dt>Subtree visibility</dt><dd>{change.beforeHidden ? 'Hidden' : 'Visible'} → {change.afterHidden ? 'Hidden' : 'Visible'}</dd></>}
      {change.before && change.before.pruned !== change.after?.pruned && <><dt>Pruned</dt><dd>{change.before.pruned ? 'Yes' : 'No'} → {change.after?.pruned ? 'Yes' : 'No'}</dd></>}
      {change.before && change.before.kept !== change.after?.kept && <><dt>Kept</dt><dd>{change.before.kept ? 'Yes' : 'No'} → {change.after?.kept ? 'Yes' : 'No'}</dd></>}
      {change.before?.attemptId && change.before.attemptId !== change.after?.attemptId
        && <><dt>Attempt</dt><dd>New draft attempt; existing evidence is archived.</dd></>}
    </dl>
  </li>
}

function ProposalCard({ workspace, proposal, precondition, disabled, onResolve }: ProposalCardProps) {
  const styles = useTreeAssistantPanelStyles()
  const workspaceJson = JSON.stringify(workspace)
  const proposalJson = JSON.stringify(proposal)
  const preconditionJson = JSON.stringify(precondition)
  const { preview, error } = useMemo(() => {
    const saved = JSON.parse(proposalJson) as TreeAssistantProposal
    if (saved.status !== 'pending') return { preview: undefined, error: '' }
    try { return { preview: prepareAssistantProposal(JSON.parse(workspaceJson) as TreeWorkspace, saved,
      preconditionJson ? JSON.parse(preconditionJson) as TreeAssistantPrecondition : undefined), error: '' } }
    catch (failure: unknown) { return { preview: undefined, error: toApiError(failure).detail } }
  }, [workspaceJson, proposalJson, preconditionJson])
  const action = proposal.action
  const label = action.kind === 'run' ? 'Approve run' : action.kind === 'score' ? 'Approve scoring'
    : action.kind === 'plan' && action.run ? 'Approve plan and run' : 'Approve edits'
  if (proposal.status !== 'pending') return <details className={styles.resolved} aria-label="Resolved assistant proposal">
    <summary className={styles.disclosure}>
      <Text weight="semibold">{proposal.status === 'applied' ? 'Applied' : proposal.status === 'rejected' ? 'Rejected' : 'Failed'}</Text>
      <Text> · {proposal.summary}</Text>
    </summary>
    <Text className={styles.userText}>{proposal.result?.detail}</Text>
    <details><summary className={styles.disclosure}>Action details (JSON)</summary>
      <pre className={styles.json} aria-label="Resolved action JSON" tabIndex={0}>{JSON.stringify(action, null, 2)}</pre>
    </details>
  </details>
  return <section className={styles.proposal} aria-label="Assistant proposal">
    <div className={styles.proposalBody} role="group" aria-label="Proposal preview" tabIndex={0}>
      <Text weight="semibold">{proposal.summary}</Text>
      {preview && <><Text>{preview.description}</Text>
        <Text weight="semibold">{preview.operations === 0 ? 'Draft / tree edits only — no execution' : preview.kind === 'score' ? 'Score saved responses' : 'Run approved nodes'}</Text>
        <Text>{preview.operations} planned target / converter / scorer operations.</Text>
        <Text>Affected nodes: {preview.affectedNodeIds.length} ({preview.affectedNodeIds.length - preview.addedNodeIds.length} existing, {preview.addedNodeIds.length} new).</Text>
        {(preview.kind === 'mutate' || preview.kind === 'plan') && <ul className={styles.changes} aria-label="Exact node changes">
          {preview.changes.map((change: TreeAssistantNodeChange) => <NodeChange key={change.nodeId} change={change} />)}
        </ul>}
      </>}
      <details><summary className={styles.disclosure}>Action details (JSON)</summary>
        {preview && <div className={styles.message}>
          <Text className={styles.identifiers}>Affected IDs: {preview.affectedNodeIds.join(', ') || 'None'}</Text>
          <Text className={styles.identifiers}>{preview.kind === 'score' ? 'Exact scoring IDs' : 'Exact runnable IDs'}: {preview.nodeIds.join(', ') || 'None — drafts only'}</Text>
          <ul className={styles.changes} aria-label="Reviewed node identities">{preview.changes.map((change: TreeAssistantNodeChange) => <li key={change.nodeId}>
            <Text>{promptLabel(change.after?.prompt ?? change.before?.prompt ?? '')}</Text>
            <Text className={styles.identifiers}> · {change.nodeId} · parent {change.after?.parentId ?? 'Root'}</Text>
            {change.after?.attemptId && <Text className={styles.identifiers}> · attempt {change.after.attemptId}</Text>}
          </li>)}</ul>
        </div>}
        <pre className={styles.json} role="region" aria-label="Proposed action JSON" tabIndex={0}>{JSON.stringify(action, null, 2)}</pre>
      </details>
      {error && <MessageBar intent="warning" layout="multiline"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
    </div>
    {proposal.status === 'pending' && <div className={styles.actions}>
      <Button className={styles.button} appearance="primary" disabled={disabled || !preview} onClick={() => { void onResolve(proposal, true, preview) }}>{label}</Button>
      <Button className={styles.button} disabled={disabled} onClick={() => { void onResolve(proposal, false) }}>Reject</Button>
    </div>}
  </section>
}

function TurnCard({ turn, ...props }: Omit<ProposalCardProps, 'proposal'> & { turn: TreeAssistantTurn }) {
  const styles = useTreeAssistantPanelStyles()
  return <li className={styles.stack}>
    <div className={styles.message}><Text weight="semibold">You</Text><Text className={styles.userText}>{turn.message}</Text></div>
    <div className={styles.message}><Text weight="semibold">Assistant</Text><MarkdownContent content={turn.reply} /></div>
    <TreeAssistantTurnDetails turn={turn} />
    {turn.proposals.map((proposal) => <ProposalCard key={proposal.id} {...props} proposal={proposal} />)}
  </li>
}

export default function TreeAssistantPanel(props: TreeAssistantPanelProps) {
  const { workspace, active, disabled } = props
  const styles = useTreeAssistantPanelStyles()
  const chat = useTreeAssistantSession(props)
  const [confirmation, setConfirmation] = useState<'detach' | 'interrupted' | 'clear' | null>(null)
  const [archiveOpen, setArchiveOpen] = useState(false)
  const [exportError, setExportError] = useState('')
  const { checkpoint } = chat
  const unavailable = disabled || !active || chat.busy || !!chat.storageError || !chat.writable
  const locked = unavailable || !chat.connected || chat.journal.kind !== 'ready'
  const pendingMessage = checkpoint.pendingMessage

  function exportChat(): boolean {
    try {
      downloadTextFile(exportAssistantChat(checkpoint), `tree-chat-${workspace.id}.json`, 'application/json')
      setExportError('')
      return true
    } catch (failure) { setExportError(toApiError(failure).detail); return false }
  }

  return <aside aria-label="Tree assistant" hidden={!active} className={styles.root}>
    <header className={styles.header}>
      <h2 className={styles.title}>Tree assistant</h2>
      <div className={styles.actions}>
        {!chat.connected && <Button className={styles.button} appearance="primary" disabled={unavailable}
          onClick={() => { void chat.start() }}>{checkpoint.session ? 'Resume session' : 'Start session'}</Button>}
        <Menu>
          <MenuTrigger disableButtonEnhancement><Button className={styles.button} appearance="subtle" icon={<MoreHorizontalRegular />} aria-label="Session actions" /></MenuTrigger>
          <MenuPopover><MenuList>
            <MenuItem onClick={exportChat}>Export chat</MenuItem>
            {chat.connected && <>
              <MenuItem disabled={unavailable || chat.journal.kind === 'receipt' || chat.journal.kind === 'execution'}
                onClick={() => { void chat.start(true, true) }}>Restart with context</MenuItem>
              <MenuItem disabled={unavailable || chat.journal.kind === 'receipt' || chat.journal.kind === 'execution'}
                onClick={() => { void chat.start(true) }}>Reset session</MenuItem>
            </>}
          </MenuList></MenuPopover>
        </Menu>
      </div>
    </header>
    <div className={styles.content}><div className={styles.stack}>
      <details><summary className={styles.disclosure}>Session details · {chat.connected ? 'Connected' : 'Offline'}</summary>
        <Text className={styles.muted}>Chat is saved in this browser; recent turns restore model context. Tree details are retrieved on demand by read-only tools, without approval. Resuming never replays actions. Planned operations exclude provider retries and tokens.</Text>
        {checkpoint.session && <Text className={styles.muted}>Model: {checkpoint.session.model} · Journal: {chat.journal.kind}</Text>}
      </details>
      {checkpoint.session && !chat.connected && <Text>Saved conversation loaded. Resume to reconnect or restore its context.</Text>}
      {[chat.error, chat.writerError, exportError].filter(Boolean).map((error) => <MessageBar key={error} intent="error" layout="multiline" role="alert"><MessageBarBody>{error}</MessageBarBody></MessageBar>)}
      {chat.storageError && <MessageBar intent="error" layout="multiline" role="alert"><MessageBarBody>
        Chat could not be saved. {chat.storageError} Export local context before leaving.
        <Button className={styles.button} disabled={!chat.writable} onClick={() => { void chat.retrySave() }}>Retry saving chat</Button>
        <Button className={styles.button} disabled={!chat.writable || chat.busy} onClick={() => {
          if (exportChat()) setConfirmation('clear')
        }}>Export and clear local chat</Button>
      </MessageBarBody></MessageBar>}
      {!!checkpoint.archivedTurns?.length && <details open={archiveOpen} onToggle={(event) => { setArchiveOpen(event.currentTarget.open) }}>
        <summary className={styles.disclosure}>Earlier conversation ({checkpoint.archivedTurns.length} archived turns)</summary>
        <Text>Retained locally and included in exports, not sent when restoring model context.</Text>
        {archiveOpen && <ol className={styles.transcript}>{checkpoint.archivedTurns.map((turn) =>
          <TurnCard key={turn.request_id} turn={turn} workspace={workspace} disabled onResolve={chat.decide} />)}</ol>}
      </details>}
      <ol className={styles.transcript} role="log" aria-label="Assistant conversation" aria-live="polite">
        {checkpoint.session?.turns.map((turn) =>
          <TurnCard key={turn.request_id} turn={turn} workspace={workspace} precondition={checkpoint.preconditions?.[turn.request_id]}
            disabled={locked} onResolve={chat.decide} />)}
        {pendingMessage && <li className={styles.message}><Text weight="semibold">You</Text><Text className={styles.userText}>{pendingMessage.message}</Text></li>}
      </ol>
      {pendingMessage && !chat.busy && <div className={styles.actions}>
        <Button className={styles.button} disabled={unavailable || !chat.connected} onClick={() => { void chat.retryMessage() }}>Retry message</Button>
        <Button className={styles.button} disabled={unavailable} onClick={() => { void chat.recoverReply() }}>Check for reply</Button>
      </div>}
      {checkpoint.executing && !chat.busy && <MessageBar layout="multiline" intent="warning"><MessageBarBody>
        Interrupted action: its outcome may be partial or unknown. Inspect the tree/backend before continuing. It will not be replayed.
        <Button className={styles.button} disabled={unavailable} onClick={() => { setConfirmation('interrupted') }}>I reviewed the interrupted action</Button>
      </MessageBarBody></MessageBar>}
      {checkpoint.unreported && <div className={styles.stack}>
        <Text role="status">{checkpoint.unreported.error ? `Result not reported. ${checkpoint.unreported.error}` : 'Reporting result…'}</Text>
        <Button className={styles.button} disabled={unavailable} onClick={() => { void chat.retryReporting() }}>Retry reporting result</Button>
        <Button className={styles.button} disabled={unavailable} onClick={() => { setConfirmation('detach') }}>Discard unreported receipt</Button>
      </div>}
      {chat.busy && <Spinner size="tiny" label="Working…" />}
    </div></div>
    <form className={styles.composer} onSubmit={(event) => { event.preventDefault(); void chat.send() }}>
      <Field label="Message"><Textarea className={styles.input} value={checkpoint.draft} disabled={locked}
        rows={3} resize="vertical" maxLength={32_000} onChange={(_, data) => { chat.editDraft(data.value) }} /></Field>
      <Button className={styles.button} type="submit" appearance="primary" disabled={locked || !checkpoint.draft.trim()}>Send message</Button>
    </form>
    {confirmation && <Dialog open={active} onOpenChange={(_, data) => { if (!data.open) setConfirmation(null) }}>
      <DialogSurface><DialogBody>
        <DialogTitle>{confirmation === 'interrupted' ? 'Confirm interrupted action review'
          : confirmation === 'clear' ? 'Confirm saved chat export' : 'Discard unreported receipt?'}</DialogTitle>
        <DialogContent>
          {confirmation === 'clear' ? <Text>The browser was asked to download your retained chat. Confirm only after verifying the file was saved. If the download was blocked or canceled, cancel this dialog. Clearing removes the local transcript and interrupted-action or unreported receipt records; review uncertain outcomes in the tree/backend. No action is replayed or undone.</Text>
            : <Text>No action will be replayed or undone. Preserve the chat export and inspect backend evidence before discarding uncertain reporting state.</Text>}
          {checkpoint.unreported && <pre className={styles.json}>{JSON.stringify(checkpoint.unreported.receipt, null, 2)}</pre>}
        </DialogContent>
        <DialogActions>
          <Button className={styles.button} onClick={() => { setConfirmation(null) }}>Cancel</Button>
          <Button className={styles.button} disabled={confirmation === 'clear' ? !chat.writable || chat.busy : unavailable} onClick={() => {
            const action = confirmation
            setConfirmation(null)
            if (action === 'interrupted') void chat.reviewInterrupted()
            else if (action === 'clear') chat.clearExportedChat()
            else chat.detach()
          }}>{confirmation === 'interrupted' ? 'Confirm review without replay'
              : confirmation === 'clear' ? 'I saved the export; clear local chat' : 'Discard receipt and detach chat'}</Button>
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>}
  </aside>
}
