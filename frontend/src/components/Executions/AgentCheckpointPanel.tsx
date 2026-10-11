import { useState } from 'react'

import { Button, Checkbox, MessageBar, Text } from '@fluentui/react-components'

import { executionsApi } from '@/services/executions'
import { toApiError } from '@/services/errors'
import type { AgentCheckpointStatus, AgentExecution } from '@/types'

import { useAgentCheckpointPanelStyles } from './AgentCheckpointPanel.styles'

interface AgentCheckpointPanelProps {
  readonly executionId: string
  readonly onChanged: (execution?: AgentExecution) => void
}

export default function AgentCheckpointPanel({ executionId, onChanged }: AgentCheckpointPanelProps) {
  const styles = useAgentCheckpointPanelStyles()
  const [status, setStatus] = useState<AgentCheckpointStatus | null>(null)
  const [consent, setConsent] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const act = async (operation: 'inspect' | 'save' | 'restore' | 'delete'): Promise<void> => {
    if (busy || (operation === 'save' && !consent)) return
    setBusy(true); setError(null); setNotice(null)
    try {
      if (operation === 'save') {
        const checkpoint = await executionsApi.saveCheckpoint(executionId)
        setStatus({ checkpoint, save_available: false, restore_available: true, detail: 'Sensitive native state retained.' })
        setNotice('Checkpoint saved. This allocation is closed; restore explicitly to continue the same chat.')
        onChanged()
      } else if (operation === 'restore') {
        const execution = await executionsApi.restoreCheckpoint(executionId)
        setStatus((previous) => previous ? {
          ...previous, restore_available: false, detail: 'A new allocation exists; this checkpoint cannot rewind the chat.',
        } : null)
        setNotice('Restored into a new allocation. No prompt was sent.')
        onChanged(execution)
      } else if (operation === 'delete') {
        setStatus(await executionsApi.deleteCheckpoint(executionId))
        setConfirmDelete(false)
        setNotice('Private checkpoint state deleted. Conversation evidence is unchanged.')
      } else {
        setStatus(await executionsApi.checkpoint(executionId))
      }
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
      if (operation !== 'inspect') onChanged()
    } finally { setBusy(false) }
  }

  return <section className={styles.root} aria-label="Execution checkpoint">
    <Button disabled={busy} onClick={() => { void act('inspect') }}>
      {status ? 'Refresh checkpoint status' : 'Checkpoint options'}
    </Button>
    {error && <MessageBar intent="error">{error}</MessageBar>}
    {notice && <MessageBar intent="success">{notice}</MessageBar>}
    {status && <>
      <Text>{status.detail}</Text>
      {status.save_available && <Checkbox checked={consent} disabled={busy}
        label="I agree to retain sensitive native session and workspace state until explicitly deleted."
        onChange={(_, data) => setConsent(data.checked === true)} />}
      {status.checkpoint && <Text size={200}>
        Saved {new Date(status.checkpoint.created_at).toLocaleString()} through event {status.checkpoint.event_cursor}.
        {' '}Restore continues only the unchanged same chat; no rewind or forks.
      </Text>}
      <div className={styles.actions}>
        {status.save_available && <Button disabled={busy || !consent} onClick={() => { void act('save') }}>Save and close</Button>}
        {status.checkpoint && <>
          <Button disabled={busy || !status.restore_available} onClick={() => { void act('restore') }}>Restore same chat</Button>
        </>}
        {(status.checkpoint || status.deletion_pending) && <>
          <Button disabled={busy} onClick={() => {
            if (confirmDelete) void act('delete')
            else setConfirmDelete(true)
          }}>{confirmDelete ? 'Confirm checkpoint deletion' : status.deletion_pending ? 'Retry checkpoint deletion' : 'Delete checkpoint'}</Button>
        </>}
      </div>
      <Text size={200}>Only completed-turn Docker application state is supported. External tool effects are not rolled back.
        {' '}Failed restore allocations remain visible and cannot be silently retried.</Text>
    </>}
  </section>
}
