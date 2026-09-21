import { useEffect, useEffectEvent, useRef, useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, MessageBar, MessageBarBody, Select, Text, Textarea,
} from '@fluentui/react-components'

import type { ConverterCatalogEntry, TargetInstance, TreeCommand, TreeConverterSpec, TreeNode, TreeWorkspace } from '@/types'

import { getTreePath, validateTreeForkPath } from './treeModel'
import TreePipelineEditor from './TreePipelineEditor'
import { useTreeForkPathDialogStyles } from './TreeForkPathDialog.styles'

interface TreeForkPathDialogProps {
  readonly workspace: TreeWorkspace
  readonly selectedId: string
  readonly open: boolean
  readonly catalog: ConverterCatalogEntry[]
  readonly targets: TargetInstance[]
  readonly onClose: () => void
  readonly onFork: (command: Extract<TreeCommand, { type: 'forkPath' }>) => Promise<boolean>
  readonly onPreview?: (nodeIds: string[]) => void
}

const SAVE_ERROR = 'The path could not be saved. Your changes are still here; try again.'
const OPTION_PROMPT_LENGTH = 70

function ancestors(workspace: TreeWorkspace, nodeId: string): TreeNode[] {
  const result: TreeNode[] = []
  const visited = new Set<string>()
  let current = workspace.nodes.find((node: TreeNode) => node.id === nodeId)
  while (current && !visited.has(current.id)) {
    visited.add(current.id)
    result.unshift(current)
    const parentId = current.parentId
    current = workspace.nodes.find((node: TreeNode) => node.id === parentId)
  }
  return result
}

function optionLabel(node: TreeNode): string {
  const prompt = node.prompt.length > OPTION_PROMPT_LENGTH
    ? `${node.prompt.slice(0, OPTION_PROMPT_LENGTH)}…` : node.prompt
  return `${prompt} (${node.id})${node.pruned ? ' — pruned' : ''}${node.status === 'running' ? ' — running' : ''}`
}

function TreeForkPathForm({
  workspace, selectedId, catalog, targets, onClose, onFork, onPreview,
}: Omit<TreeForkPathDialogProps, 'open'>) {
  const styles = useTreeForkPathDialogStyles()
  const selected = workspace.nodes.find((node: TreeNode) => node.id === selectedId)
  const [startId, setStartId] = useState(selectedId)
  const [descendantId, setDescendantId] = useState(selectedId)
  const [prompt, setPrompt] = useState(selected?.prompt ?? '')
  const [pipeline, setPipeline] = useState<TreeConverterSpec[]>(selected?.converters ?? [])
  const [editingPipeline, setEditingPipeline] = useState(false)
  const [busy, setBusy] = useState(false)
  const [saveError, setSaveError] = useState('')
  const saving = useRef(false)
  const mounted = useRef(true)
  const command: Extract<TreeCommand, { type: 'forkPath' }> = {
    type: 'forkPath', nodeId: startId, descendantId, prompt, converters: pipeline,
  }
  const starts = ancestors(workspace, descendantId)
  const endpoints = workspace.nodes.filter((node: TreeNode) =>
    ancestors(workspace, node.id).some((ancestor: TreeNode) => ancestor.id === startId))
  let path: TreeNode[] = []
  let validationError = ''
  try {
    path = getTreePath(workspace, startId, descendantId)
    validateTreeForkPath(workspace, command)
  } catch (failure: unknown) {
    validationError = failure instanceof Error ? failure.message : 'Choose a valid ancestor-to-descendant path.'
  }
  const previewKey = JSON.stringify(path.map((node: TreeNode) => node.id))
  const emitPreview = useEffectEvent((nodeIds: string[]) => { onPreview?.(nodeIds) })

  useEffect(() => {
    const nodeIds: string[] = JSON.parse(previewKey)
    emitPreview(nodeIds)
    return () => { emitPreview([]) }
  }, [previewKey])

  useEffect(() => {
    mounted.current = true
    return () => { mounted.current = false }
  }, [])

  function close(): void {
    if (saving.current) return
    onPreview?.([])
    onClose()
  }

  function changeStart(nodeId: string): void {
    const source = workspace.nodes.find((node: TreeNode) => node.id === nodeId)
    setStartId(nodeId)
    setPrompt(source?.prompt ?? '')
    setPipeline(source?.converters ?? [])
    setEditingPipeline(false)
    setSaveError('')
  }

  async function fork(): Promise<void> {
    if (saving.current || editingPipeline || validationError) return
    saving.current = true
    setBusy(true)
    setSaveError('')
    try {
      const saved = await onFork(command)
      if (!mounted.current) return
      if (saved) {
        onPreview?.([])
        onClose()
      } else setSaveError(SAVE_ERROR)
    } catch {
      if (mounted.current) setSaveError(SAVE_ERROR)
    } finally {
      saving.current = false
      if (mounted.current) setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(_: unknown, data: { open: boolean }) => { if (!data.open) close() }}>
      <DialogSurface className={styles.surface}>
        <DialogBody>
          <DialogTitle>Fork path</DialogTitle>
          <DialogContent className={styles.content}>
            <Text>
              Copy one ancestor-to-descendant path into new drafts. The original nodes, responses, and scores stay unchanged.
              This dialog does not send prompts; workspace auto-run settings apply after saving.
            </Text>
            <div className={styles.selectors}>
              <Field label="Starting ancestor">
                <Select className={styles.input} value={startId} disabled={busy} onChange={(_: unknown, data: { value: string }) => { changeStart(data.value) }}>
                  {!starts.some((node: TreeNode) => node.id === startId) && <option value={startId}>Node unavailable</option>}
                  {starts.map((node: TreeNode) => <option key={node.id} value={node.id}>{optionLabel(node)}</option>)}
                </Select>
              </Field>
              <Field label="Path endpoint">
                <Select className={styles.input} value={descendantId} disabled={busy} onChange={(_: unknown, data: { value: string }) => {
                  setDescendantId(data.value)
                  setSaveError('')
                }}>
                  {!endpoints.some((node: TreeNode) => node.id === descendantId) && <option value={descendantId}>Node unavailable</option>}
                  {endpoints.map((node: TreeNode) => <option key={node.id} value={node.id}>{optionLabel(node)}</option>)}
                </Select>
              </Field>
            </div>
            <Field label="First prompt">
              <Textarea className={styles.input} value={prompt} resize="vertical" disabled={busy}
                onChange={(_: unknown, data: { value: string }) => { setPrompt(data.value); setSaveError('') }} />
            </Field>
            <section className={styles.section} aria-label="First prompt converter pipeline">
              <Text weight="semibold">First prompt converter pipeline</Text>
              <TreePipelineEditor key={startId} converters={pipeline} catalog={catalog} targets={targets} disabled={busy}
                onChange={(next: TreeConverterSpec[]) => { setPipeline(next); setSaveError('') }}
                onEditingChange={setEditingPipeline} />
              <Text className={styles.muted}>Other prompts keep their saved converter pipelines.</Text>
            </section>
            <section className={styles.section} aria-label="Path preview">
              <Text weight="semibold">{path.length} {path.length === 1 ? 'prompt' : 'prompts'} will be copied as drafts.</Text>
              <Text className={styles.muted}>
                Side branches and nodes beyond the endpoint are excluded ({Math.max(0, endpoints.length - path.length)} nodes).
              </Text>
              <ol className={styles.preview} aria-label="Prompts to copy">
                {path.map((node: TreeNode, index: number) => (
                  <li key={node.id} className={styles.step}>
                    <Text className={styles.muted}>{node.id}{index === 0 ? ' — new branch root' : ''}</Text>
                    <p className={styles.prompt}>{index === 0 ? prompt : node.prompt}</p>
                    <Text className={styles.muted}>
                      Converters: {(index === 0 ? pipeline : node.converters).map((converter: TreeConverterSpec) => converter.type).join(' → ') || 'None'}
                    </Text>
                  </li>
                ))}
              </ol>
            </section>
            {validationError && <MessageBar intent="error"><MessageBarBody>{validationError}</MessageBarBody></MessageBar>}
            {editingPipeline && <Text>Apply or cancel converter changes before forking.</Text>}
            {saveError && <MessageBar intent="error"><MessageBarBody>{saveError}</MessageBarBody></MessageBar>}
          </DialogContent>
          <DialogActions className={styles.actions}>
            <Button className={styles.button} disabled={busy} onClick={close}>Cancel</Button>
            <Button className={styles.button} appearance="primary" disabled={busy || editingPipeline || !!validationError}
              onClick={() => { void fork() }}>{busy ? 'Saving path…' : 'Fork path'}</Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}

/** Only stages a canonical command; persistence and any workspace auto-run belong to the controller. */
export default function TreeForkPathDialog({ open, ...props }: TreeForkPathDialogProps) {
  return open ? <TreeForkPathForm key={props.workspace.id} {...props} /> : null
}
