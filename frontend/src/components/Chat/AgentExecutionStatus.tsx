import { useEffect, useState } from 'react'

import { Badge, Button, MessageBar, MessageBarBody, Text } from '@fluentui/react-components'

import type { ConversationExecution } from '@/types'

import { useAgentActivityStyles } from './AgentActivity.styles'

interface AgentExecutionStatusProps {
  readonly execution: ConversationExecution | null
  readonly feed: string
  readonly disabled: boolean
  readonly cancelling: boolean
  readonly onCancel: () => Promise<void>
  readonly onControl: (action: 'continue' | 'extend' | 'close') => Promise<void>
}

export default function AgentExecutionStatus({
  execution, feed, disabled, cancelling, onCancel, onControl,
}: AgentExecutionStatusProps) {
  const styles = useAgentActivityStyles()
  const [now, setNow] = useState(Date.now)
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  const control = async (action: 'continue' | 'extend' | 'close'): Promise<void> => {
    setBusy(true)
    try { await onControl(action) } finally { setBusy(false) }
  }
  const remaining = execution?.held_until ? Math.max(0, Math.ceil((Date.parse(execution.held_until) - now) / 1000)) : null
  const pending = execution?.approvals?.filter((approval) => approval.decision === null).length ?? 0

  return (
    <section className={styles.status} aria-label="Agent connection status">
      <div className={styles.controls}>
        <Badge appearance="outline">Feed: {feed}</Badge>
        <Badge appearance="outline">ACP: {execution?.connection_state ?? 'not started'}</Badge>
        <Badge appearance="outline">Environment: {execution ? `${execution.environment} / ${execution.state}` : 'not provisioned'}</Badge>
        {pending > 0 && <Badge color="warning">Awaiting {pending} approval{pending === 1 ? '' : 's'}</Badge>}
        {(execution?.state === 'working' || execution?.state === 'starting') && (
          <Button disabled={disabled || cancelling} onClick={() => { void onCancel() }}>Cancel turn</Button>
        )}
        {execution?.interactive && execution.state !== 'closed' && (
          <Button disabled={disabled || busy} onClick={() => { void control('close') }}>Close execution</Button>
        )}
      </div>
      {execution?.state === 'held' && (
        <MessageBar intent="warning"><MessageBarBody>
          Turn cancelled; the running environment is retained for {remaining}s. No rollback or snapshot occurred.
          <div className={styles.controls}>
            <Button disabled={disabled || busy || remaining === 0} onClick={() => { void control('continue') }}>Continue</Button>
            <Button disabled={disabled || busy || remaining === 0} onClick={() => { void control('extend') }}>Extend hold</Button>
          </div>
        </MessageBarBody></MessageBar>
      )}
      <details><summary>Connection details</summary>
        <Text block>Last retained event: {execution?.last_event_at ? new Date(execution.last_event_at).toLocaleTimeString() : 'None yet'}</Text>
        <Text block>Absolute execution expiry: {execution?.expires_at ? new Date(execution.expires_at).toLocaleString() : 'Not started'}</Text>
        <Text block>{execution?.source_coverage ?? 'Saving a configuration does not start the harness.'}</Text>
        {execution?.capture_error && <Text block>{execution.capture_error}</Text>}
      </details>
    </section>
  )
}
