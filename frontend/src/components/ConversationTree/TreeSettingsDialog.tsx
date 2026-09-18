import { useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface,
  DialogTitle, Field, Input, MessageBar, MessageBarBody, Select, Text,
} from '@fluentui/react-components'

import type { TreeSettings } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'

interface TreeSettingsDialogProps {
  settings: TreeSettings
  open: boolean
  onClose: () => void
  onSave: (settings: TreeSettings) => Promise<boolean>
}

export default function TreeSettingsDialog({ settings, open, onClose, onSave }: TreeSettingsDialogProps) {
  const styles = useConversationTreeStyles()
  const [draft, setDraft] = useState(settings)
  const [budget, setBudget] = useState(String(settings.operationBudget))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  async function save(): Promise<void> {
    const amount = Number(budget)
    if (!Number.isSafeInteger(amount) || amount < 1 || amount > 100_000) {
      setError('Enter an operation budget between 1 and 100000.')
      return
    }
    setSaving(true)
    if (await onSave({ ...draft, operationBudget: amount })) onClose()
    setSaving(false)
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open && !saving) onClose() }}>
      <DialogSurface><DialogBody>
        <DialogTitle>Workspace settings</DialogTitle>
        <DialogContent className={styles.stack}>
          <Field label="Traversal">
            <Select value={draft.traversal} onChange={(_, data) => {
              if (data.value === 'breadth-first' || data.value === 'depth-first') setDraft({ ...draft, traversal: data.value })
            }}>
              <option value="breadth-first">Breadth-first: compare each level</option>
              <option value="depth-first">Depth-first: pursue each branch</option>
            </Select>
          </Field>
          <Field label="Maximum concurrent requests" hint="BFS finishes each level before advancing. Shared judges are serialized. Targets not verified for parallel use run sequentially.">
            <Select value={String(draft.concurrency ?? 1)} onChange={(_, data) => {
              const concurrency = Number(data.value)
              if (concurrency === 1 || concurrency === 2 || concurrency === 4) setDraft({ ...draft, concurrency })
            }}>
              <option value="1">1 (sequential)</option><option value="2">2</option><option value="4">4</option>
            </Select>
          </Field>
          <Field label="Edge style">
            <Select value={draft.edgeStyle ?? 'bezier'} onChange={(_, data) => {
              if (data.value === 'bezier' || data.value === 'smoothstep' || data.value === 'straight') setDraft({ ...draft, edgeStyle: data.value })
            }}>
              <option value="bezier">Curved (Bezier)</option><option value="smoothstep">Rounded step</option><option value="straight">Straight</option>
            </Select>
          </Field>
          <Field label="Default node size">
            <Select value={draft.nodeSize ?? 'standard'} onChange={(_, data) => {
              if (data.value === 'compact' || data.value === 'standard' || data.value === 'expanded') setDraft({ ...draft, nodeSize: data.value })
            }}>
              <option value="compact">Compact</option><option value="standard">Standard</option><option value="expanded">Expanded preview</option>
            </Select>
          </Field>
          <Field label="Per-run operation budget" hint="Target sends + converter applications + automatic scoring requests. Provider retries, per-message scoring and composites may cost more.">
            <Input type="number" min={1} max={100000} value={budget} onChange={(_, data) => { setBudget(data.value) }} />
          </Field>
          <Checkbox label="Ask before running" checked={draft.confirmRuns} onChange={(_, data) => { setDraft({ ...draft, confirmRuns: data.checked === true }) }} />
          <Checkbox label="Auto-run newly added branches" checked={draft.autoRun} onChange={(_, data) => { setDraft({ ...draft, autoRun: data.checked === true }) }} />
          <Text className={styles.muted}>Disabling confirmations only affects explicit Run/Retry actions. Auto-run separately authorizes sends when adding branches.</Text>
          <Checkbox label="Continue independent branches after an error" checked={draft.continueOnError} onChange={(_, data) => { setDraft({ ...draft, continueOnError: data.checked === true }) }} />
          <Checkbox label="Render responses as Markdown" checked={draft.markdown} onChange={(_, data) => { setDraft({ ...draft, markdown: data.checked === true }) }} />
          <Checkbox label="Stack new sample groups" checked={draft.stackSamples} onChange={(_, data) => { setDraft({ ...draft, stackSamples: data.checked === true }) }} />
          <Checkbox label="Stack new prompt/pipeline variant groups" checked={draft.stackVariants} onChange={(_, data) => { setDraft({ ...draft, stackVariants: data.checked === true }) }} />
          <Text className={styles.muted}>Changes apply to future runs. Imported plans do not change these preferences. Stacking is a view, not pruning.</Text>
          {error && <MessageBar layout="multiline" intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
        </DialogContent>
        <DialogActions>
          <Button className={styles.button} disabled={saving} onClick={onClose}>Cancel</Button>
          <Button className={styles.button} appearance="primary" disabled={saving} onClick={() => { void save() }}>Save settings</Button>
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>
  )
}
