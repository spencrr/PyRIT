import { useMemo, useState } from 'react'
import { Badge, Button, Field, MessageBar, Slider, Text } from '@fluentui/react-components'

import AgentActivity from '@/components/Chat/AgentActivity'
import { executionsApi } from '@/services/executions'
import { toApiError } from '@/services/errors'
import type { AgentEvidenceSnapshot } from '@/types'
import { reduceAgentEvents } from '@/utils/agentActivity'
import { downloadBlob } from '@/utils/conversationExport'
import { useAgentEvidencePanelStyles } from './AgentEvidencePanel.styles'

interface AgentEvidencePanelProps {
  readonly executionId: string
  readonly closed: boolean
}

export default function AgentEvidencePanel({ executionId, closed }: AgentEvidencePanelProps) {
  const styles = useAgentEvidencePanelStyles()
  const [snapshot, setSnapshot] = useState<AgentEvidenceSnapshot | null>(null)
  const [count, setCount] = useState(0)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const events = snapshot?.events
  const activity = useMemo(() => reduceAgentEvents({}, events?.slice(0, count) ?? []), [events, count])
  const load = async (): Promise<void> => {
    setBusy(true); setError(null)
    try {
      const result = await executionsApi.evidence(executionId)
      if (result.execution.id !== executionId) throw new Error('Recording identity does not match the execution')
      setSnapshot(result); setCount(0)
    } catch (cause: unknown) { setError(toApiError(cause).detail) }
    finally { setBusy(false) }
  }
  const download = async (): Promise<void> => {
    setBusy(true); setError(null)
    try {
      downloadBlob(await executionsApi.downloadEvidence(executionId), `pyrit-execution-${executionId}.zip`)
    } catch (cause: unknown) { setError(toApiError(cause).detail) }
    finally { setBusy(false) }
  }
  const last = events?.[count - 1]
  return <section className={styles.root} aria-label="Execution evidence">
    <div className={styles.actions}>
      <Button disabled={!closed || busy} onClick={() => { void load() }}>Inspect recording</Button>
      <Button disabled={!closed || busy} onClick={() => { void download() }}>Download evidence package</Button>
    </div>
    <Text size={200}>{closed
      ? 'Admin export includes recorded prompts, tool data and retained files. Treat it as sensitive.'
      : 'Close the execution to export its settled recording. This action does not close it for you.'}</Text>
    {error && <MessageBar intent="error">{error}</MessageBar>}
    {snapshot && <>
      <Text weight="semibold">Read-only playback · no tools run</Text>
      <Text size={200}>Captured {new Date(snapshot.captured_at).toLocaleString()} through event {snapshot.event_cursor}.
        {' '}{snapshot.messages.length} stored messages · {snapshot.scores.length} scores.</Text>
      <div className={styles.actions}>
        {Object.entries(snapshot.assessment).map(([name, assessment]) => <Badge key={name} appearance="outline">
          {name}: {assessment.status}
        </Badge>)}
      </div>
      <details><summary>Capabilities and limitations</summary>
        {Object.entries(snapshot.assessment).map(([name, value]) => <Text key={name} block>{name}: {value.detail}</Text>)}
        {snapshot.omissions.map((value, index) => <Text key={`${index}-${value}`} block>{value}</Text>)}
      </details>
      <Field label={`Recorded events: ${count} of ${snapshot.events.length}`}>
        <Slider min={0} max={Math.max(1, snapshot.events.length)} value={count}
          disabled={snapshot.events.length === 0} onChange={(_, data) => setCount(data.value)} />
      </Field>
      <div className={styles.actions}>
        <Button disabled={count === 0} onClick={() => setCount((value) => value - 1)}>Previous event</Button>
        <Button disabled={count === snapshot.events.length} onClick={() => setCount((value) => value + 1)}>Next event</Button>
        <Button disabled={count === snapshot.events.length} onClick={() => setCount(snapshot.events.length)}>Show all events</Button>
      </div>
      {snapshot.execution.turns.filter((turn) => activity[turn.id] || count === snapshot.events.length).map((turn) => {
        const finished = count === snapshot.events.length || snapshot.events.slice(0, count).some((event) => event.turn_id === turn.id
          && event.direction === 'lifecycle' && event.payload.type === 'turn_finished')
        return <AgentActivity key={turn.id} disabled turn={finished ? turn : {
          ...turn, status: 'running', response_text: '', capture_complete: false, error: null,
        }} activity={activity[turn.id]} />
      })}
      {last && <details><summary>Event {last.sequence} · {last.direction}</summary>
        <pre className={styles.payload}>{JSON.stringify(last.payload, null, 2)}</pre>
      </details>}
    </>}
  </section>
}
