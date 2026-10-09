import { useEffect, useState } from 'react'

import {
  Button, MessageBar, MessageBarBody,
  Table, TableBody, TableCell, TableHeader, TableHeaderCell, TableRow, Text,
  makeStyles, tokens,
} from '@fluentui/react-components'
import { Link as RouterLink, useSearchParams } from 'react-router'

import AgentTargetDialog from '@/components/Config/AgentTargetDialog'
import { executionsApi } from '@/services/executions'
import { toApiError } from '@/services/errors'
import type { AgentExecution, AgentExecutionEvent, AgentTargetConfiguration, TargetInstance } from '@/types'

const useStyles = makeStyles({
  root: { padding: tokens.spacingHorizontalXL, overflowY: 'auto', height: '100%', minWidth: 0 },
  form: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalM, marginBlock: tokens.spacingVerticalL },
  actions: { display: 'flex', gap: tokens.spacingHorizontalS, marginBlock: tokens.spacingVerticalM, flexWrap: 'wrap' },
  table: { overflowX: 'auto' },
  event: {
    whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', fontFamily: tokens.fontFamilyMonospace,
    padding: tokens.spacingHorizontalS, backgroundColor: tokens.colorNeutralBackground2,
  },
})

interface ExecutionsProps {
  readonly canManage: boolean
  readonly onTargetCreated: (target: TargetInstance) => void
}

function eventSummary(event: AgentExecutionEvent): string {
  const params = event.payload.params
  if (params && typeof params === 'object' && 'update' in params) {
    const update = params.update
    if (update && typeof update === 'object' && 'sessionUpdate' in update) {
      const title = 'title' in update && typeof update.title === 'string' ? `: ${update.title}` : ''
      const status = 'status' in update && typeof update.status === 'string' ? ` (${update.status})` : ''
      return `${String(update.sessionUpdate)}${title}${status}`
    }
  }
  return String(event.payload.method ?? event.payload.type ?? 'response')
}

export default function Executions({ canManage, onTargetCreated }: ExecutionsProps) {
  const styles = useStyles()
  const [searchParams] = useSearchParams()
  const conversationId = searchParams.get('conversation') ?? undefined
  const [records, setRecords] = useState<AgentExecution[]>([])
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [selected, setSelected] = useState<AgentExecution | null>(null)
  const [events, setEvents] = useState<AgentExecutionEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  const [listError, setListError] = useState<string | null>(null)
  const [eventError, setEventError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [offset, setOffset] = useState(0)
  const [copyConfiguration, setCopyConfiguration] = useState<AgentTargetConfiguration | null>(null)

  useEffect(() => {
    if (!canManage) return
    let stopped = false
    let timer: ReturnType<typeof setTimeout>
    const poll = async (): Promise<void> => {
      try {
        const page = await executionsApi.list(offset, conversationId)
        if (!stopped) {
          setRecords(page)
          setListError(null)
          if (conversationId && page[0]) setSelectedId((previous: string | null) => previous ?? page[0].id)
        }
      } catch (cause: unknown) {
        if (!stopped) setListError(toApiError(cause).detail)
      } finally {
        if (!stopped) timer = setTimeout(() => { void poll() }, 1500)
      }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [canManage, offset, conversationId])

  useEffect(() => {
    if (!canManage || !selectedId) return
    let stopped = false
    let cursor = 0
    let timer: ReturnType<typeof setTimeout>
    const poll = async (): Promise<void> => {
      try {
        const [record, page] = await Promise.all([
          executionsApi.get(selectedId), executionsApi.events(selectedId, cursor),
        ])
        if (stopped) return
        setSelected(record)
        setEventError(null)
        setEvents((previous: AgentExecutionEvent[]) => [...previous, ...page.events].slice(-500))
        cursor = page.next_cursor
      } catch (cause: unknown) {
        if (!stopped) setEventError(toApiError(cause).detail)
      } finally {
        if (!stopped) timer = setTimeout(() => { void poll() }, 500)
      }
    }
    void poll()
    return () => { stopped = true; clearTimeout(timer) }
  }, [canManage, selectedId])

  const control = async (action: 'cancel' | 'close'): Promise<void> => {
    if (!selectedId) return
    setBusy(true)
    setError(null)
    try {
      const record = await executionsApi[action](selectedId)
      setSelected(record)
      if (record.cleanup_error) setError(record.cleanup_error)
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
    } finally { setBusy(false) }
  }

  const select = (record: AgentExecution): void => {
    setSelectedId(record.id)
    setSelected(record)
    setEvents([])
  }

  if (!canManage) return <MessageBar intent="warning">Administrator access is required to manage agent executions.</MessageBar>

  return (
    <section className={styles.root} aria-label="Agent executions">
      <Text as="h1" size={600}>Agent executions</Text>
      <p>Fresh ACP sessions and workspaces. Observation does not renew expiry. Local execution is not sandboxed.</p>
      {(error || listError || eventError) && <MessageBar intent="error"><MessageBarBody>{error || listError || eventError}</MessageBarBody></MessageBar>}
      {notice && <MessageBar intent="success"><MessageBarBody>{notice} <RouterLink to="/chat">Open chat</RouterLink></MessageBarBody></MessageBar>}
      <p>Configure reusable targets in <RouterLink to="/registry/targets">Registry</RouterLink> or directly from New Chat. This view manages running resources.</p>
      <div className={styles.table}>
        <Table aria-label="Owned executions">
          <TableHeader><TableRow>
            <TableHeaderCell>Execution</TableHeaderCell><TableHeaderCell>Profile</TableHeaderCell>
            <TableHeaderCell>Environment</TableHeaderCell><TableHeaderCell>Resource state</TableHeaderCell>
            <TableHeaderCell>Last turn</TableHeaderCell>
          </TableRow></TableHeader>
          <TableBody>{records.map((record: AgentExecution) => <TableRow key={record.id}>
            <TableCell><Button appearance="subtle" onClick={() => select(record)}>{record.id.slice(0, 8)}</Button></TableCell>
            <TableCell>{record.configuration.name}</TableCell><TableCell>{record.configuration.environment_template.environment}</TableCell>
            <TableCell>{record.state}</TableCell><TableCell>{record.turns[record.turns.length - 1]?.status ?? 'No turn'}</TableCell>
          </TableRow>)}</TableBody>
        </Table>
      </div>
      <div className={styles.actions}>
        <Button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
        <Button disabled={records.length < 50} onClick={() => setOffset(offset + 50)}>Next</Button>
      </div>
      {selected && <section aria-label="Execution details">
        <Text as="h2">{selected.configuration.name}: {selected.state}</Text>
        <p>Conversation: {selected.conversation_id}</p>
        <p>Created: {new Date(selected.created_at).toLocaleString()}. Maximum lifetime: {selected.profile.lifetime_seconds}s. Idle timeout: {selected.profile.idle_timeout_seconds}s.</p>
        <p>{selected.source_coverage}</p>
        <p>Capture: {selected.capture_error ?? (selected.turns[selected.turns.length - 1]?.capture_complete ? 'Complete for the last turn' : 'In progress or incomplete')}</p>
        {selected.cleanup_error && <MessageBar intent="error">{selected.cleanup_error}</MessageBar>}
        <div className={styles.actions}>
          <Button disabled={busy || selected.state !== 'working'} onClick={() => { void control('cancel') }}>Cancel turn</Button>
          <Button disabled={busy || selected.state === 'closed'} onClick={() => { void control('close') }}>
            {selected.state === 'cleanup_failed' ? 'Retry cleanup' : 'Close execution'}
          </Button>
          <Button disabled={busy} onClick={() => setCopyConfiguration({
            ...selected.configuration,
            environment_template: {
              ...selected.configuration.environment_template,
              image: selected.image_id ?? selected.configuration.environment_template.image,
              expected_fixture_sha256: selected.fixture_sha256,
            },
          })}>Copy configuration to new target</Button>
        </div>
        <p>A new target configuration starts fresh on the next conversation; it does not resume this execution.</p>
        <p>Retained artifacts: {selected.artifacts.join(', ') || 'None'}</p>
        {selected.artifact_errors.map((message: string) => <MessageBar key={message} intent="warning">{message}</MessageBar>)}
        <details><summary>Resolved configuration</summary><pre className={styles.event}>{JSON.stringify(selected.configuration, null, 2)}</pre></details>
        <Text as="h3">Live recorded events (latest 500 displayed)</Text>
        {events.map((event: AgentExecutionEvent) => <details key={event.sequence}>
          <summary>{event.sequence} · {event.direction} · {eventSummary(event)}</summary>
          <pre className={styles.event}>{JSON.stringify(event.payload, null, 2)}</pre>
        </details>)}
      </section>}
      {copyConfiguration && <AgentTargetDialog initialConfiguration={copyConfiguration}
        onClose={() => setCopyConfiguration(null)} onCreated={(target: TargetInstance) => {
          setCopyConfiguration(null)
          onTargetCreated(target)
          setNotice(`Registered ${target.target_registry_name}. Select it in a new chat or scanner run.`)
        }} />}
    </section>
  )
}
