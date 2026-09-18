import type { TreeAssistantToolCall, TreeAssistantTurn } from '@/types'

import { useTreeAssistantTurnDetailsStyles } from './TreeAssistantTurnDetails.styles'

interface TreeAssistantTurnDetailsProps {
  readonly turn: TreeAssistantTurn
}

function formatDuration(durationMs: number): string {
  return `${Number(durationMs.toFixed(1))} ms`
}

/** Recorded tools and supplied context only: never inferred metrics or hidden model reasoning. */
export default function TreeAssistantTurnDetails({ turn }: TreeAssistantTurnDetailsProps) {
  const styles = useTreeAssistantTurnDetailsStyles()
  const [open, setOpen] = useState(false)
  const context = turn.context_summary
  const usage = turn.usage
  const hasUsage = usage && (usage.input_tokens !== undefined || usage.output_tokens !== undefined || usage.total_tokens !== undefined)
  const summary = [
    turn.tool_calls === undefined ? 'Turn details' : `Tools (${turn.tool_calls.length})`,
    context ? 'context' : null,
    hasUsage ? 'token usage' : null,
  ].filter(Boolean).join(' · ')

  return (
    <details className={styles.root} open={open} onToggle={(event) => { setOpen(event.currentTarget.open) }}>
      <summary className={styles.summary}>{summary}</summary>
      {open && <div className={styles.body}>
        {turn.tool_calls === undefined && (
          <p className={styles.muted}>Tool trace unavailable. Older turns may not include recorded tools.</p>
        )}
        {turn.tool_calls?.length === 0 && <p className={styles.muted}>No tool calls were recorded for this turn.</p>}
        {turn.tool_calls?.map((tool: TreeAssistantToolCall) => (
          <details key={tool.id} className={styles.tool}>
            <summary className={styles.summary}>{tool.name} · {tool.status} · {formatDuration(tool.duration_ms)}</summary>
            <div className={styles.body}>
              <dl className={styles.fields}>
                <dt>Tool call ID</dt><dd>{tool.id}</dd>
                <dt>Status</dt><dd>{tool.status}</dd>
                <dt>Duration</dt><dd>{formatDuration(tool.duration_ms)}</dd>
              </dl>
              <section className={styles.section} aria-label={`${tool.name} arguments`}>
                <h4 className={styles.heading}>Arguments</h4>
                <pre className={styles.text} tabIndex={0} aria-label={`${tool.name} argument text`}>{JSON.stringify(tool.arguments, null, 2)}</pre>
              </section>
              <section className={styles.section} aria-label={`${tool.name} result`}>
                <h4 className={styles.heading}>Result</h4>
                {tool.truncated && <p className={styles.muted}>The server marked this result as truncated. All recorded text is shown below.</p>}
                <pre className={styles.text} tabIndex={0} aria-label={`${tool.name} result text`}>{tool.result}</pre>
              </section>
            </div>
          </details>
        ))}
        {context ? (
          <section className={styles.section} aria-label="Turn context">
            <h4 className={styles.heading}>Context supplied to the assistant</h4>
            <dl className={styles.fields}>
              <dt>Workspace</dt><dd>{context.workspace_id}</dd>
              <dt>Revision</dt><dd>{context.revision}</dd>
              <dt>Selected node</dt><dd>{context.selected_node_id ?? 'None'}</dd>
              <dt>Node count</dt><dd>{context.node_count}</dd>
              <dt>Model</dt><dd>{context.model}</dd>
              <dt>API</dt><dd>{context.api}</dd>
              <dt>Restored history</dt><dd>{context.restored ? 'Yes' : 'No'}</dd>
            </dl>
            {context.restoration_notice != null && (
              <section className={styles.section} aria-label="Restoration notice">
                <h4 className={styles.heading}>Restoration notice</h4>
                <pre className={styles.text} tabIndex={0} aria-label="Restoration notice text">{context.restoration_notice}</pre>
              </section>
            )}
            <h4 className={styles.heading}>Available tools</h4>
            {context.tools.length > 0 ? (
              <ul className={styles.tools}>{context.tools.map((name: string) => <li key={name}>{name}</li>)}</ul>
            ) : <p className={styles.muted}>No tools listed.</p>}
            <h4 className={styles.heading}>Server instructions</h4>
            <pre className={styles.text} tabIndex={0} aria-label="Server instructions text">{context.instructions}</pre>
          </section>
        ) : <p className={styles.muted}>Context summary unavailable for this turn.</p>}
        {hasUsage && (
          <section className={styles.section} aria-label="Token usage">
            <h4 className={styles.heading}>Reported token usage</h4>
            <dl className={styles.fields}>
              {usage.input_tokens !== undefined && <><dt>Input tokens</dt><dd>{usage.input_tokens}</dd></>}
              {usage.output_tokens !== undefined && <><dt>Output tokens</dt><dd>{usage.output_tokens}</dd></>}
              {usage.total_tokens !== undefined && <><dt>Total tokens</dt><dd>{usage.total_tokens}</dd></>}
            </dl>
          </section>
        )}
      </div>}
    </details>
  )
}
import { useState } from 'react'
