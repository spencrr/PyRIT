import { Badge, Button, Text } from '@fluentui/react-components'

import type { ConversationExecution } from '@/types'

import { useAgentActivityStyles } from './AgentActivity.styles'

interface AgentExecutionStatusProps {
  readonly execution: ConversationExecution | null
  readonly feed: string
  readonly disabled: boolean
  readonly cancelling: boolean
  readonly onCancel: () => Promise<void>
}

export default function AgentExecutionStatus({
  execution, feed, disabled, cancelling, onCancel,
}: AgentExecutionStatusProps) {
  const styles = useAgentActivityStyles()
  return (
    <section className={styles.status} aria-label="Agent connection status">
      <div className={styles.controls}>
        <Badge appearance="outline">Feed: {feed}</Badge>
        <Badge appearance="outline">ACP: {execution?.connection_state ?? 'not started'}</Badge>
        <Badge appearance="outline">Environment: {execution ? `${execution.environment} / ${execution.state}` : 'not provisioned'}</Badge>
        {(execution?.state === 'working' || execution?.state === 'starting') && (
          <Button disabled={disabled || cancelling} onClick={() => { void onCancel() }}>Cancel turn</Button>
        )}
      </div>
      <details><summary>Connection details</summary>
        <Text block>{execution?.source_coverage ?? 'Saving a configuration does not start the harness.'}</Text>
        {execution?.capture_error && <Text block>{execution.capture_error}</Text>}
      </details>
    </section>
  )
}
