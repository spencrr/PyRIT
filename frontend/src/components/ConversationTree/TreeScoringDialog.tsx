import { useEffect, useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface, DialogTitle,
  Field, MessageBar, MessageBarBody, Select, Text, Textarea,
} from '@fluentui/react-components'

import ParameterField from '@/components/Parameters/ParameterField'
import { getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { scorersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScorerCatalogEntry, ScorerInstance, TargetInstance, TreeSettings } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { buildScorerParameters } from './scorerForm'

interface TreeScoringDialogProps {
  settings: TreeSettings
  targets: TargetInstance[]
  open: boolean
  onClose: () => void
  onSave: (settings: TreeSettings) => Promise<boolean>
}

function textValue(value: ParameterFormValue | undefined): string {
  return typeof value === 'string' ? value : ''
}

export default function TreeScoringDialog({ settings, targets, open, onClose, onSave }: TreeScoringDialogProps) {
  const styles = useConversationTreeStyles()
  const [draft, setDraft] = useState(settings)
  const [catalog, setCatalog] = useState<ScorerCatalogEntry[]>([])
  const [instances, setInstances] = useState<ScorerInstance[]>([])
  const [type, setType] = useState('')
  const [judge, setJudge] = useState('')
  const [values, setValues] = useState<Record<string, ParameterFormValue>>({})
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const selected = catalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === type)
  const unsupportedRequired = selected?.parameters.filter((parameter) => parameter.required && parameter.input_kind === 'unsupported') ?? []
  useEffect(() => {
    if (!open) return
    let cancelled = false
    Promise.all([scorersApi.listCatalog(), scorersApi.listScorers()]).then(([types, registered]) => {
      if (!cancelled) { setCatalog(types.items); setInstances(registered.items) }
    }).catch((failure: unknown) => { if (!cancelled) setError(toApiError(failure).detail) })
    return () => { cancelled = true }
  }, [open])
  function add(instance: ScorerInstance): void {
    if (draft.scorers.some((scorer) => scorer.scorer_id === instance.scorer_id)) return
    setDraft({
      ...draft,
      primaryScorerId: draft.primaryScorerId ?? instance.scorer_id,
      scorers: [...draft.scorers, { ...instance, scope: 'response', highIsRisk: true }],
    })
  }
  async function create(): Promise<void> {
    if (!selected) return
    const result = buildScorerParameters(selected.parameters.filter((parameter) => parameter.name !== 'chat_target'), values)
    if (!result.ok) { setError(result.error); return }
    if (selected.is_llm_based && !judge) { setError('Select a registered judge target.'); return }
    setBusy(true)
    setError('')
    try {
      const instance = await scorersApi.createScorer({ type, params: { ...result.parameters, ...(judge ? { chat_target: judge } : {}) } })
      setInstances([...instances.filter((item) => item.scorer_id !== instance.scorer_id), instance])
      add(instance)
      setType('')
    } catch (failure) { setError(toApiError(failure).detail) }
    finally { setBusy(false) }
  }
  return (
    <Dialog open={open} onOpenChange={(_, data) => { if (!data.open && !busy) onClose() }}>
      <DialogSurface className={styles.dialog}><DialogBody>
        <DialogTitle>Workspace scoring</DialogTitle>
        <DialogContent className={styles.stack}>
          <Field label="Evaluation objective or rubric">
            <Textarea value={draft.objective} rows={3} onChange={(_, data) => { setDraft({ ...draft, objective: data.value }) }} />
          </Field>
          <Field label="Add registered scorer">
            <Select value="" disabled={busy} onChange={(_, data) => {
              const instance = instances.find((item: ScorerInstance) => item.scorer_id === data.value)
              if (instance) add(instance)
            }}>
              <option value="">Choose a registered scorer</option>
              {instances.map((instance) => <option key={instance.scorer_id} value={instance.scorer_id}>{instance.scorer_type} ({instance.scorer_id})</option>)}
            </Select>
          </Field>
          {draft.scorers.map((scorer) => (
            <section className={styles.branchCard} key={scorer.scorer_id} aria-label={scorer.scorer_type}>
              <Text weight="semibold">{scorer.scorer_type}</Text>
              <Text className={styles.muted}>{scorer.score_type} · {scorer.identifier_hash.slice(0, 12)}</Text>
              <Field label={`Evidence for ${scorer.scorer_type}`} hint="All applicable messages invokes the scorer separately for each stored message; it is not a joint conversation verdict.">
                <Select value={scorer.scope} onChange={(_, data) => {
                  if (data.value !== 'response' && data.value !== 'conversation') return
                  const scope = data.value
                  setDraft({ ...draft, scorers: draft.scorers.map((item) => item.scorer_id === scorer.scorer_id ? { ...item, scope } : item) })
                }}>
                  <option value="response">Current response</option>
                  <option value="conversation">All applicable messages</option>
                </Select>
              </Field>
              <Checkbox label="Higher values indicate more risk" checked={scorer.highIsRisk} onChange={(_, data) => {
                setDraft({ ...draft, scorers: draft.scorers.map((item) => item.scorer_id === scorer.scorer_id ? { ...item, highIsRisk: data.checked === true } : item) })
              }} />
              <Button className={styles.button} onClick={() => {
                const remaining = draft.scorers.filter((item) => item.scorer_id !== scorer.scorer_id)
                setDraft({ ...draft, scorers: remaining,
                  primaryScorerId: draft.primaryScorerId === scorer.scorer_id ? remaining[0]?.scorer_id : draft.primaryScorerId,
                  autoScore: remaining.length > 0 && draft.autoScore })
              }}>Remove scorer</Button>
            </section>
          ))}
          <Field label="Primary node metric">
            <Select value={draft.primaryScorerId ?? ''} onChange={(_, data) => { setDraft({ ...draft, primaryScorerId: data.value || undefined }) }}>
              <option value="">First selected scorer</option>
              {draft.scorers.map((scorer) => <option key={scorer.scorer_id} value={scorer.scorer_id}>{scorer.scorer_type}</option>)}
            </Select>
          </Field>
          <Checkbox label="Score automatically after each response" checked={draft.autoScore} disabled={draft.scorers.length === 0}
            onChange={(_, data) => { setDraft({ ...draft, autoScore: data.checked === true }) }} />
          <details className={styles.disclosure}>
            <summary>Configure a new scorer</summary>
            <div className={styles.stack}>
              <Field label="Scorer type">
                <Select value={type} disabled={busy} onChange={(_, data) => {
                  setType(data.value)
                  const entry = catalog.find((item) => item.scorer_type === data.value)
                  setValues(getInitialFormValues(entry?.parameters ?? [], undefined, { prefillDefaults: false }))
                  setJudge('')
                  setError('')
                }}>
                  <option value="">Choose a scorer type</option>
                  {catalog.map((entry) => <option key={entry.scorer_type} value={entry.scorer_type}>{entry.scorer_type}{entry.is_llm_based ? ' (LLM)' : ''}</option>)}
                </Select>
              </Field>
              {selected?.is_llm_based && <Field label="Judge target" required hint="Choose the registered model that will evaluate responses.">
                <Select value={judge} disabled={busy} onChange={(_, data) => { setJudge(data.value) }}>
                  <option value="">Choose a judge target</option>
                  {targets.map((target) => <option key={target.target_registry_name} value={target.target_registry_name}>{target.target_registry_name}</option>)}
                </Select>
              </Field>}
              {selected?.parameters.filter((parameter) => parameter.name !== 'chat_target' && parameter.input_kind !== 'unsupported').map((parameter) =>
                parameter.input_kind === 'json' || parameter.input_kind === 'multiline'
                  ? <div key={parameter.name} className={styles.stack}>
                    <Field label={parameter.name} required={parameter.required}
                      hint={parameter.input_kind === 'json' ? `${parameter.type_name} as JSON. ${parameter.description ?? ''}` : parameter.description ?? undefined}>
                      <Textarea rows={parameter.input_kind === 'json' ? 6 : 4} disabled={busy}
                        value={textValue(values[parameter.name])}
                        onChange={(_, data) => { setValues({ ...values, [parameter.name]: data.value }) }} />
                    </Field>
                    {parameter.example && <Button className={styles.button} disabled={busy} onClick={() => {
                      setValues({ ...values, [parameter.name]: parameter.example ?? '' })
                    }}>Use example {parameter.name}</Button>}
                    {parameter.json_schema && <details className={styles.disclosure}>
                      <summary>{parameter.name} schema</summary>
                      <pre className={styles.parameters}>{JSON.stringify(parameter.json_schema, null, 2)}</pre>
                    </details>}
                  </div>
                  : <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''} disabled={busy}
                    onChange={(name, value) => { setValues({ ...values, [name]: value }) }} />)}
              {unsupportedRequired.length > 0 && <MessageBar intent="warning"><MessageBarBody>
                This scorer requires Python-only configuration for {unsupportedRequired.map((parameter) => parameter.name).join(', ')}.
                Register it in an initializer, then select the registered scorer above.
              </MessageBarBody></MessageBar>}
              <Button className={styles.button} disabled={busy || !selected || unsupportedRequired.length > 0} onClick={() => { void create() }}>Create and select scorer</Button>
            </div>
          </details>
          <Text className={styles.muted}>Scoring uses persisted backend evidence and may invoke a judge model. Per-message scopes and composite scorers can make multiple calls per scoring request. Values are verdicts, not confidence probabilities.</Text>
          {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
        </DialogContent>
        <DialogActions>
          <Button className={styles.button} disabled={busy} onClick={onClose}>Cancel</Button>
          <Button className={styles.button} appearance="primary" disabled={busy} onClick={() => { void onSave(draft).then((saved) => { if (saved) onClose() }) }}>Save scoring settings</Button>
        </DialogActions>
      </DialogBody></DialogSurface>
    </Dialog>
  )
}
