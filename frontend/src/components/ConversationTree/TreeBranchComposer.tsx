import { useRef, useState } from 'react'

import { Button, Field, MessageBar, MessageBarBody, Tab, TabList, Text, Textarea } from '@fluentui/react-components'
import { AddRegular, CopyRegular, DeleteRegular } from '@fluentui/react-icons'

import type { ConverterCatalogEntry, TargetInstance, TreeCommand, TreeConverterSpec, TreeNode } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import TreePipelineEditor from './TreePipelineEditor'
import { MAX_FAN_OUT } from './treeModel'

interface PromptDraft {
  id: number
  prompt: string
}

interface PipelineDraft {
  id: number
  converters: TreeConverterSpec[]
}

interface TreeBranchComposerProps {
  node: TreeNode
  catalog: ConverterCatalogEntry[]
  targets: TargetInstance[]
  disabled: boolean
  autoRun: boolean
  onCommand: (command: TreeCommand) => Promise<boolean>
  onDirtyChange: (dirty: boolean) => void
}

export default function TreeBranchComposer({
  node, catalog, targets, disabled: externallyDisabled, autoRun, onCommand, onDirtyChange,
}: TreeBranchComposerProps) {
  const styles = useConversationTreeStyles()
  const [mode, setMode] = useState<'prompts' | 'converters'>('prompts')
  const [prompts, setPrompts] = useState<PromptDraft[]>([{ id: 0, prompt: '' }])
  const [pipelines, setPipelines] = useState<PipelineDraft[]>([{ id: 0, converters: [] }, { id: 1, converters: [] }])
  const [nextId, setNextId] = useState(2)
  const [followUp, setFollowUp] = useState('')
  const [editingPipeline, setEditingPipeline] = useState<number | null>(null)
  const [dirty, setDirty] = useState(false)
  const contentDirty = useRef(false)
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const pending = useRef(false)
  const disabled = externallyDisabled || submitting
  const canContinue = node.status !== 'error' && node.status !== 'running'

  function markDirty(): void {
    contentDirty.current = true
    setDirty(true)
    onDirtyChange(true)
  }

  function clear(): void {
    setPrompts([{ id: nextId, prompt: '' }])
    setPipelines([{ id: nextId + 1, converters: [] }, { id: nextId + 2, converters: [] }])
    setNextId(nextId + 3)
    setFollowUp('')
    setDirty(false)
    contentDirty.current = false
    setEditingPipeline(null)
    setError('')
    onDirtyChange(false)
  }

  async function submit(): Promise<void> {
    if (pending.current) return
    const variants = mode === 'prompts'
      ? prompts.map((draft: PromptDraft) => ({ prompt: draft.prompt, converters: [] }))
      : pipelines.map((pipeline: PipelineDraft) => ({ prompt: followUp, converters: pipeline.converters }))
    if (variants.some((variant) => !variant.prompt.trim())) {
      setError('Enter a prompt for every child branch.')
      return
    }
    const command: TreeCommand = { type: 'childVariants', nodeId: node.id, variants }
    pending.current = true
    setSubmitting(true)
    try { if (await onCommand(command)) clear() }
    finally { pending.current = false; setSubmitting(false) }
  }

  const branchCount = mode === 'prompts' ? prompts.length : pipelines.length
  const operation = autoRun ? 'Add & run' : 'Add'
  return (
    <section className={styles.stack} aria-label="Branch composer">
      <Text as="h3" size={400}>Branch</Text>
      <TabList selectedValue={mode} onTabSelect={(_, data) => {
        if (data.value === 'prompts' || data.value === 'converters') {
          setMode(data.value)
          setError('')
        }
      }}>
        <Tab value="prompts" disabled={disabled || ((dirty || editingPipeline !== null) && mode !== 'prompts')}>Prompts</Tab>
        <Tab value="converters" disabled={disabled || ((dirty || editingPipeline !== null) && mode !== 'converters')}>Pipelines</Tab>
      </TabList>
      {mode === 'prompts' && (
        <>
          <Text className={styles.muted}>Each prompt is a child, continuing from this response.</Text>
          {prompts.map((draft: PromptDraft, index: number) => (
            <div className={styles.branchCard} key={draft.id}>
              <Field label={`Child prompt ${index + 1}`}>
                <Textarea value={draft.prompt} rows={3} disabled={disabled || !canContinue} onChange={(_, data) => {
                  setPrompts(prompts.map((item: PromptDraft) => item.id === draft.id ? { ...item, prompt: data.value } : item))
                  markDirty()
                }} />
              </Field>
              <div className={styles.row}>
                <Button className={styles.button} icon={<CopyRegular />} disabled={disabled || !canContinue || prompts.length >= MAX_FAN_OUT}
                  aria-label={`Duplicate child prompt ${index + 1}`} onClick={() => {
                    setPrompts([...prompts, { id: nextId, prompt: draft.prompt }]); setNextId(nextId + 1); markDirty()
                  }}>Duplicate</Button>
                <Button className={styles.button} icon={<DeleteRegular />} disabled={disabled || prompts.length === 1}
                  aria-label={`Remove child prompt ${index + 1}`} onClick={() => {
                    setPrompts(prompts.filter((item: PromptDraft) => item.id !== draft.id)); markDirty()
                  }} />
              </div>
            </div>
          ))}
          <Button className={styles.button} icon={<AddRegular />} disabled={disabled || !canContinue || prompts.length >= MAX_FAN_OUT}
            onClick={() => { setPrompts([...prompts, { id: nextId, prompt: '' }]); setNextId(nextId + 1); markDirty() }}>Add prompt variant</Button>
        </>
      )}
      {mode === 'converters' && (
        <>
          <Field label="Shared child prompt" hint="One child per pipeline. Steps within a pipeline run in order.">
            <Textarea value={followUp} rows={3} disabled={disabled || !canContinue}
              onChange={(_, data) => { setFollowUp(data.value); markDirty() }} />
          </Field>
          {pipelines.map((pipeline: PipelineDraft, index: number) => (
            <section className={styles.branchCard} key={pipeline.id} aria-label={`Pipeline ${index + 1}`}>
              <div className={styles.row}>
                <Text weight="semibold">Pipeline {index + 1}</Text>
                <Button className={styles.button} icon={<CopyRegular />} disabled={disabled || editingPipeline !== null || pipelines.length >= MAX_FAN_OUT}
                  aria-label={`Duplicate pipeline ${index + 1}`} onClick={() => {
                    setPipelines([...pipelines, { id: nextId, converters: pipeline.converters }])
                    setNextId(nextId + 1); markDirty()
                  }} />
                <Button className={styles.button} icon={<DeleteRegular />} disabled={disabled || editingPipeline !== null || pipelines.length === 1}
                  aria-label={`Remove pipeline ${index + 1}`} onClick={() => {
                    setPipelines(pipelines.filter((item: PipelineDraft) => item.id !== pipeline.id)); markDirty()
                  }} />
              </div>
              <TreePipelineEditor converters={pipeline.converters} catalog={catalog} targets={targets}
                disabled={disabled || !canContinue || (editingPipeline !== null && editingPipeline !== pipeline.id)}
                onChange={(converters: TreeConverterSpec[]) => {
                  setPipelines(pipelines.map((item: PipelineDraft) => item.id === pipeline.id ? { ...item, converters } : item))
                  markDirty()
                }}
                onEditingChange={(editing: boolean) => {
                  setEditingPipeline(editing ? pipeline.id : null)
                  onDirtyChange(contentDirty.current || editing)
                }} />
            </section>
          ))}
          <Button className={styles.button} icon={<AddRegular />} disabled={disabled || !canContinue || editingPipeline !== null || pipelines.length >= MAX_FAN_OUT}
            onClick={() => { setPipelines([...pipelines, { id: nextId, converters: [] }]); setNextId(nextId + 1); markDirty() }}>Add pipeline</Button>
        </>
      )}
      {!canContinue && <Text className={styles.muted}>Retry or recover this response before continuing.</Text>}
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.row}>
        <Button className={styles.button} appearance="primary" disabled={disabled || editingPipeline !== null || !canContinue}
          onClick={() => { void submit() }}>
          {operation} {branchCount} {branchCount === 1 ? 'child' : 'children'}
        </Button>
        {dirty && <Button className={styles.button} disabled={disabled} onClick={clear}>Discard branch edits</Button>}
      </div>
    </section>
  )
}
