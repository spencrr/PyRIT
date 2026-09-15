import { useRef, useState } from 'react'

import { Badge, Button, Divider, Field, Link, MessageBar, MessageBarBody, Select, Switch, Text, Textarea } from '@fluentui/react-components'
import { ArrowClockwiseRegular } from '@fluentui/react-icons'

import MessageList from '@/components/Chat/MessageList'
import type { ConverterCatalogEntry, TargetInstance, TreeCommand, TreeConverterSpec, TreeNode, TreeSettings } from '@/types'
import { backendMessagesToFrontend } from '@/utils/messageMapper'
import { attackConversationRoutePath } from '@/utils/routeParams'

import { useConversationTreeStyles } from './ConversationTree.styles'
import TreeBranchComposer from './TreeBranchComposer'
import TreePipelineEditor from './TreePipelineEditor'
import TreeSampleControl from './TreeSampleControl'
import TreeScoreMeter from './TreeScoreMeter'

interface TreeNodeEditorProps {
  node: TreeNode
  hidden: boolean
  catalog: ConverterCatalogEntry[]
  targets?: TargetInstance[]
  disabled: boolean
  hasChildren?: boolean
  autoRun?: boolean
  active?: boolean
  onCommand: (command: TreeCommand) => Promise<boolean>
  onRun: (nodeId: string) => void
  onDirtyChange?: (dirty: boolean) => void
  onRecover?: (nodeId: string) => void
  settings: TreeSettings
  canRun: boolean
  onMarkdownChange: (markdown: boolean) => void
  onScore: (nodeId: string, subtree: boolean) => void
}

export default function TreeNodeEditor({
  node, hidden, catalog, targets = [], disabled, hasChildren = false, autoRun = false,
  active = true, onCommand, onRun, onDirtyChange, onRecover, settings, canRun, onMarkdownChange, onScore,
}: TreeNodeEditorProps) {
  const styles = useConversationTreeStyles()
  const [prompt, setPrompt] = useState(node.prompt)
  const [converters, setConverters] = useState<TreeConverterSpec[]>(node.converters)
  const [pipelineEditing, setPipelineEditing] = useState(false)
  const [branchDirty, setBranchDirty] = useState(false)
  const [attempt, setAttempt] = useState('current')
  const historical = node.attempts?.find((item) => item.attemptId === attempt)
  const viewed = historical ?? node
  const edits = useRef({ content: false, pipeline: false, branch: false })
  const changed = prompt !== node.prompt || JSON.stringify(converters) !== JSON.stringify(node.converters)
  const inputDisabled = disabled || node.status === 'running'
  const blocked = inputDisabled || hidden || !!historical
  const pendingEdits = changed || pipelineEditing || branchDirty
  const responses = viewed.messages?.filter((message) => message.role !== 'user') ?? []
  const sentPrompt = viewed.messages?.find((message) => message.role === 'user')
    ?.message_pieces.map((piece) => piece.converted_value).join('\n')
  const canRetry = canRun && !blocked && !pendingEdits && (node.status === 'completed' || node.status === 'error')

  function reportDirty(update: Partial<typeof edits.current>): void {
    edits.current = { ...edits.current, ...update }
    onDirtyChange?.(Object.values(edits.current).some(Boolean))
  }

  function updateDraft(nextPrompt: string, nextConverters: TreeConverterSpec[]): void {
    setPrompt(nextPrompt)
    setConverters(nextConverters)
    reportDirty({ content: nextPrompt !== node.prompt || JSON.stringify(nextConverters) !== JSON.stringify(node.converters) })
  }

  return (
    <div className={styles.stack}>
      {hidden && <MessageBar><MessageBarBody>Pruned branch. Restore it or its pruned ancestor to continue.</MessageBarBody></MessageBar>}
      {(node.attempts?.length ?? 0) > 0 && <Field label="Attempt">
        <Select value={historical ? attempt : 'current'} disabled={pendingEdits} onChange={(_, data) => { setAttempt(data.value) }}>
          {node.attempts?.map((item, index) => <option key={item.attemptId} value={item.attemptId}>Attempt {index + 1}: {item.status}</option>)}
          <option value="current">Current attempt {(node.attempts?.length ?? 0) + 1}: {node.status}</option>
        </Select>
      </Field>}
      <Field label="Prompt">
        <Textarea value={historical ? historical.prompt : prompt} rows={4} disabled={inputDisabled || branchDirty || !!historical}
          onChange={(_, data) => { updateDraft(data.value, converters) }} />
      </Field>
      <details className={styles.disclosure}>
        <summary>Prompt pipeline {converters.length > 0 ? `(${converters.length} ${converters.length === 1 ? 'step' : 'steps'})` : '(none)'}</summary>
        <TreePipelineEditor converters={historical?.converters ?? converters} catalog={catalog} targets={targets}
          disabled={inputDisabled || branchDirty || !!historical}
          onChange={(pipeline: TreeConverterSpec[]) => { updateDraft(prompt, pipeline) }}
          onEditingChange={(editing: boolean) => { setPipelineEditing(editing); reportDirty({ pipeline: editing }) }} />
      </details>
      {viewed.converters.length > 0 && <details className={styles.disclosure} open>
        <summary>Sent prompt</summary>
        {sentPrompt !== undefined ? <pre className={styles.parameters}>{sentPrompt}</pre>
          : <Text className={styles.muted}>No outbound payload recorded for this attempt.</Text>}
      </details>}
      <div className={styles.row}>
        {node.status === 'draft' && changed && (
          <Button className={styles.button} appearance="primary" disabled={blocked || pipelineEditing || branchDirty || !prompt.trim()}
            onClick={() => { void onCommand({ type: 'edit', nodeId: node.id, prompt, converters }) }}>Save draft</Button>
        )}
        <Button className={styles.button} disabled={blocked || pipelineEditing || branchDirty || !prompt.trim()}
          onClick={() => { void onCommand({ type: 'fork', nodeId: node.id, prompt, converters }) }}>Fork &amp; cascade</Button>
        <Button className={styles.button} disabled={!canRun || blocked || pendingEdits || node.status === 'running' || node.status === 'error'}
          onClick={() => { onRun(node.id) }}>Run subtree</Button>
        {changed && <Button className={styles.button} disabled={disabled || pipelineEditing || branchDirty}
          onClick={() => { updateDraft(node.prompt, node.converters) }}>Discard edits</Button>}
      </div>
      <Divider />
      <section className={styles.stack} aria-label="Response">
        <div className={styles.row}>
          <Text as="h2" size={400}>Response</Text>
          <Badge appearance="tint" color={viewed.status === 'error' ? 'danger' : viewed.status === 'draft' ? 'subtle' : 'brand'}>{viewed.status}</Badge>
          <Switch label="Markdown" checked={settings.markdown} onChange={(_, data) => { onMarkdownChange(data.checked) }} />
        </div>
        {viewed.error && <MessageBar intent="error"><MessageBarBody>{viewed.error}</MessageBarBody></MessageBar>}
        {node.status === 'running' && <Text className={styles.muted}>Running or interrupted. Recover the recorded result before starting a new attempt.</Text>}
        {active && responses.length > 0
          ? <div className={styles.transcript}><MessageList messages={backendMessagesToFrontend(responses)} autoScroll={false} globalMarkdown={settings.markdown} /></div>
          : <Text className={styles.muted}>{node.status === 'draft' ? 'Not run yet.' : 'No response recorded.'}</Text>}
        <div className={styles.row}>
          <Button className={styles.button} icon={<ArrowClockwiseRegular />} disabled={!canRetry}
            onClick={() => { void onCommand({ type: 'retry', nodeId: node.id, scope: 'node' }) }}>Retry response</Button>
          {hasChildren && <Button className={styles.button} disabled={!canRetry}
            onClick={() => { void onCommand({ type: 'retry', nodeId: node.id, scope: 'subtree' }) }}>Retry subtree</Button>}
          {node.status === 'running' && node.attackResultId && node.conversationId && onRecover && (
            <Button className={styles.button} disabled={disabled || pendingEdits || !canRun}
              onClick={() => { onRecover(node.id) }}>Recover recorded result</Button>
          )}
          {active && <TreeSampleControl nodeId={node.id} disabled={blocked || pendingEdits || node.status === 'running'} autoRun={autoRun && canRun} onCommand={onCommand} />}
        </div>
        {node.status === 'error' && <Text className={styles.muted}>Retry stays in this card and retains the previous attempt. Descendants require replay against the fresh response.</Text>}
        {viewed.attackResultId && viewed.conversationId && (
          <Link href={attackConversationRoutePath(viewed.attackResultId, viewed.conversationId)} target="_blank" rel="noreferrer">
            Backend conversation
          </Link>
        )}
      </section>
      <TreeScoreMeter node={viewed} settings={settings} />
      <div className={styles.row}>
        <Button className={styles.button} disabled={!canRun || blocked || pendingEdits || !node.conversationId || settings.scorers.length === 0}
          onClick={() => { onScore(node.id, false) }}>Score response</Button>
        {hasChildren && <Button className={styles.button} disabled={!canRun || blocked || pendingEdits || settings.scorers.length === 0}
          onClick={() => { onScore(node.id, true) }}>Score subtree</Button>}
      </div>
      {viewed.scoreRuns?.map((result) => <details key={result.id} className={styles.disclosure}>
        <summary>{settings.scorers.find((scorer) => scorer.scorer_id === result.scorerId)?.scorer_type ?? result.scorerId}: {result.status}</summary>
        {result.error && <Text>{result.error}</Text>}
        {result.scores.map((score) => <div key={score.id} className={styles.stack}>
          <Text>{score.score_category?.join(', ')}: {score.score_value ?? score.status ?? 'Undetermined'}</Text>
          <Text className={styles.muted}>{score.score_rationale}</Text>
        </div>)}
      </details>)}
      <div className={styles.row}>
        <Button className={styles.button} disabled={blocked || pendingEdits || node.status !== 'completed'}
          onClick={() => { void onCommand({ type: 'keep', nodeId: node.id }) }}>Keep &amp; prune siblings</Button>
        <Button className={styles.button} disabled={disabled || pendingEdits}
          onClick={() => { void onCommand({ type: 'prune', nodeId: node.id, pruned: !node.pruned }) }}>
          {node.pruned ? 'Restore branch' : 'Prune branch'}
        </Button>
      </div>
      <Divider />
      <TreeBranchComposer node={node} catalog={catalog} targets={targets} disabled={blocked || changed || pipelineEditing}
        autoRun={autoRun && canRun} onCommand={onCommand}
        onDirtyChange={(dirty: boolean) => { setBranchDirty(dirty); reportDirty({ branch: dirty }) }} />
    </div>
  )
}
