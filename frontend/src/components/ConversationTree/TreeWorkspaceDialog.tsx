import { useState } from 'react'

import {
  Button, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, Input, MessageBar, MessageBarBody, Select, Textarea,
} from '@fluentui/react-components'

import type { TargetInstance, TreeWorkspace } from '@/types'
import { targetIdentifierHash } from '@/utils/targetIdentity'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { applyTreeCommand, createTreeWorkspace, importTreePlan } from './treeModel'

interface TreeWorkspaceDialogProps {
  targets: TargetInstance[]
  activeTarget: TargetInstance | null
  labels: Record<string, string>
  importing: boolean
  open?: boolean
  onClose: () => void
  onCreate: (workspace: TreeWorkspace) => Promise<void>
}

const PLAN_EXAMPLE = JSON.stringify({
  schemaVersion: 1,
  name: 'Multi-turn probe',
  steps: [
    { id: 'context', parentId: null, prompt: 'Describe your intended use and limitations.', converters: [] },
    { id: 'follow-up', parentId: 'context', prompt: 'Give a harmless example illustrating those limitations.', converters: [] },
  ],
}, null, 2)

export default function TreeWorkspaceDialog({
  targets, activeTarget, labels, importing, open = true, onClose, onCreate,
}: TreeWorkspaceDialogProps) {
  const styles = useConversationTreeStyles()
  const [name, setName] = useState('Conversation experiment')
  const [targetName, setTargetName] = useState(
    targets.find((target) => target.identifier.hash === activeTarget?.identifier.hash)?.target_registry_name ?? '',
  )
  const [systemPrompt, setSystemPrompt] = useState('')
  const [prompt, setPrompt] = useState('')
  const [plan, setPlan] = useState(PLAN_EXAMPLE)
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const target = targets.find((entry) => entry.target_registry_name === targetName)
  const supported = target?.capabilities?.supports_multi_turn === true
    && target.capabilities.supports_editable_history === true
    && target.capabilities.supported_input_modalities.includes('text')

  async function create(): Promise<void> {
    if (!target || !supported) {
      setError('Choose a multi-turn text target with editable history.')
      return
    }
    setSaving(true)
    try {
      const configuration = {
        name,
        targetRegistryName: targetName,
        targetIdentifierHash: targetIdentifierHash(target),
        systemPrompt: target.capabilities?.supports_system_prompt ? systemPrompt : '',
        labels,
      }
      const workspace = importing
        ? importTreePlan(plan, configuration)
        : applyTreeCommand(createTreeWorkspace(configuration), { type: 'add', parentId: null, prompt })
      await onCreate(workspace)
      onClose()
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : 'Unable to create workspace.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open && !saving) onClose() }}>
      <DialogSurface className={styles.dialog}>
        <DialogBody>
          <DialogTitle>{importing ? 'Import strategy plan' : 'New conversation tree'}</DialogTitle>
          <DialogContent className={styles.stack}>
            <Field label="Workspace name" required><Input value={name} disabled={saving} onChange={(_, data) => { setName(data.value) }} /></Field>
            <Field label="Target" required hint="Text prompts, multi-turn conversations and editable history are required. Target identity is pinned for this workspace.">
              <Select value={targetName} disabled={saving} onChange={(_, data) => { setTargetName(data.value) }}>
                <option value="">Choose a registered target</option>
                {targets.map((entry) => <option key={entry.target_registry_name} value={entry.target_registry_name}>{entry.target_registry_name}</option>)}
              </Select>
            </Field>
            {target && !supported && <MessageBar layout="multiline" intent="warning"><MessageBarBody>This target does not advertise the capabilities required for safe history branching.</MessageBarBody></MessageBar>}
            <Field label="System prompt (optional)" hint="Fixed for this workspace; create a new tree to change it.">
              <Textarea value={systemPrompt} disabled={saving || !target?.capabilities?.supports_system_prompt}
                onChange={(_, data) => { setSystemPrompt(data.value) }} rows={2} />
            </Field>
            {importing
              ? <Field label="Strategy plan JSON" hint="Versioned, declarative prompt steps. Import only stages drafts; no code or automatic model calls.">
                <Textarea className={styles.planEditor} value={plan} disabled={saving} onChange={(_, data) => { setPlan(data.value) }} />
              </Field>
              : <Field label="First prompt" required>
                <Textarea value={prompt} disabled={saving} rows={4} onChange={(_, data) => { setPrompt(data.value) }} />
              </Field>}
            {error && <MessageBar layout="multiline" intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={saving} onClick={onClose}>Cancel</Button>
            <Button className={styles.button} appearance="primary" disabled={saving || !name.trim() || !supported || (!importing && !prompt.trim())} onClick={() => { void create() }}>
              {saving ? 'Saving...' : 'Create workspace'}
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
