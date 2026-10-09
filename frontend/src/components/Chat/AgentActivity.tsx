import { Badge, Button, Text } from '@fluentui/react-components'
import MarkdownContent from '@/components/Markdown/MarkdownContent'

import type { AgentApproval, AgentToolActivity, AgentTurn, AgentTurnActivity } from '@/types'

import { useAgentActivityStyles } from './AgentActivity.styles'

interface AgentActivityProps {
  readonly turn: AgentTurn
  readonly activity?: AgentTurnActivity
  readonly approvals?: AgentApproval[]
  readonly onDecision?: (approvalId: string, allow: boolean) => Promise<void>
  readonly disabled?: boolean
}

function details(value: unknown): string {
  if (value === undefined) return 'Not reported by the harness.'
  const text = JSON.stringify(value, null, 2)
  return text.length > 12000 ? `${text.slice(0, 12000)}\n[Display truncated; retained evidence contains the full report.]` : text
}

export default function AgentActivity({ turn, activity, approvals = [], onDecision, disabled }: AgentActivityProps) {
  const styles = useAgentActivityStyles()
  const approvalCard = (approval: AgentApproval) => (
    <section key={approval.id} className={styles.tool} aria-label="Tool permission">
      <Text block weight="semibold">{approval.title}</Text>
      {approval.decision ? <Text>Permission: {approval.decision.replace(/_/g, ' ')} · {approval.actor}</Text> : <>
        <Text block>Harness requested approval. Expires {new Date(approval.expires_at).toLocaleTimeString()}.</Text>
        <div className={styles.controls}>
          <Button disabled={disabled || !onDecision || !approval.options.some((option) => option.kind === 'allow_once')}
            onClick={() => { void onDecision?.(approval.id, true) }}>Allow once</Button>
          <Button disabled={disabled || !onDecision} onClick={() => { void onDecision?.(approval.id, false) }}>Deny</Button>
        </div>
      </>}
    </section>
  )
  const toolCard = (tool: AgentToolActivity) => (
    <div key={tool.id}>
    <details className={styles.tool}>
      <summary>{tool.title} — {tool.status}</summary>
      <Text block size={200}>Tool name: {tool.name ?? 'not reported'} · ID: {tool.id}</Text>
      <Text block size={200}>Observed {new Date(tool.firstSeen).toLocaleTimeString()} – {new Date(tool.lastSeen).toLocaleTimeString()}</Text>
      <Text block weight="semibold">Input</Text><pre className={styles.raw}>{details(tool.rawInput)}</pre>
      <Text block weight="semibold">Output</Text><pre className={styles.raw}>{details(tool.rawOutput ?? tool.content)}</pre>
      {tool.locations !== undefined && <><Text block>Affected locations (agent-reported)</Text><pre className={styles.raw}>{details(tool.locations)}</pre></>}
    </details>
    {approvals.filter((approval) => approval.turn_id === turn.id && approval.tool_call_id === tool.id).map(approvalCard)}
    </div>
  )
  return (
    <section className={styles.root} aria-label="Agent turn activity">
      <Text weight="semibold">Agent activity <Badge appearance="tint">{turn.status}</Badge></Text>
      {approvals.filter((approval) => approval.turn_id === turn.id
        && !activity?.tools.some((tool) => tool.id === approval.tool_call_id)).map(approvalCard)}
      {activity?.inference && <details>
        <summary>Model inference through PyRIT ({Object.keys(activity.inference).length} requests)</summary>
        {Object.entries(activity.inference).map(([id, inference]) => <Text key={id} block size={200}>
          {id.slice(0, 8)} · {inference.status} · target {inference.target?.slice(0, 8) ?? 'not reported'}
          {inference.bytes !== undefined ? ` · ${inference.bytes} bytes` : ''}
        </Text>)}
      </details>}
      {activity?.blocks ? activity.blocks.map((block) => {
        if (block.kind === 'text') return <div key={block.id}><MarkdownContent content={block.text} /></div>
        if (block.kind === 'plan') return <details key={block.id}><summary>Agent plan</summary><pre className={styles.raw}>{details(block.entries)}</pre></details>
        const tool = activity.tools.find((item) => item.id === block.toolId)
        return tool ? toolCard(tool) : null
      }) : activity?.tools.map(toolCard)}
      {!activity?.blocks && activity?.text && (turn.status === 'running' || !turn.response_text) && <>
        {turn.status !== 'running' && <Text size={200}>Retained streamed output (not a completed reply)</Text>}
        <pre className={styles.raw}>{activity.text}</pre>
      </>}
      {activity?.blocks && activity.text && turn.status !== 'running' && !turn.response_text && <Text size={200}>
        Retained streamed output (not a completed reply)
      </Text>}
      {turn.status === 'completed' && !turn.response_text && <Text>Completed without a text reply. Inspect tool activity and retained artifacts.</Text>}
      {turn.status === 'cancelled' && <Text>Turn cancelled. Earlier tool effects are not rolled back.</Text>}
      {turn.error && <Text>{turn.error}</Text>}
      {turn.status !== 'running' && <Text size={200}>
        {turn.capture_complete ? 'Exposed ACP events retained; internal tool coverage is not guaranteed.' : 'Capture is incomplete. Missing events do not establish that an action did not occur.'}
      </Text>}
    </section>
  )
}
