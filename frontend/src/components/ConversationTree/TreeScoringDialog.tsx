import { useEffect, useState } from 'react'

import {
  Button,
  Checkbox,
  Dialog,
  DialogActions,
  DialogBody,
  DialogContent,
  DialogSurface,
  DialogTitle,
  Field,
  MessageBar,
  MessageBarBody,
  Select,
  Text,
  Textarea,
} from '@fluentui/react-components'

import { scorersApi } from '@/services/api'
import { toApiError } from '@/services/errors'
import type { ScorerCatalogEntry, ScorerInstance, TargetInstance, TreeSettings } from '@/types'

import ScorerBuilder from './ScorerBuilder'
import { useConversationTreeStyles } from './ConversationTree.styles'
import {
  buildScorerParameters,
  findUnsupportedScorerConfiguration,
  getInitialScorerFormValues,
  getScorerBuilderParameters,
  type ScorerFormValue,
} from './scorerForm'

interface TreeScoringDialogProps {
  settings: TreeSettings
  targets: TargetInstance[]
  open: boolean
  onClose: () => void
  onSave: (settings: TreeSettings) => Promise<boolean>
}

interface StatusMessage {
  intent: 'error' | 'success' | 'warning'
  text: string
}

export default function TreeScoringDialog({ settings, targets, open, onClose, onSave }: TreeScoringDialogProps) {
  const styles = useConversationTreeStyles()
  const [draft, setDraft] = useState(settings)
  const [catalog, setCatalog] = useState<ScorerCatalogEntry[]>([])
  const [instances, setInstances] = useState<ScorerInstance[]>([])
  const [type, setType] = useState('')
  const [values, setValues] = useState<Record<string, ScorerFormValue>>({})
  const [status, setStatus] = useState<StatusMessage | null>(null)
  const [busy, setBusy] = useState(false)

  const selected = catalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === type)
  const blockedConfiguration = findUnsupportedScorerConfiguration(selected, values, catalog)

  useEffect(() => {
    if (!open) {
      return
    }
    let cancelled = false
    Promise.all([scorersApi.listCatalog(), scorersApi.listScorers()])
      .then(([types, registered]) => {
        if (!cancelled) {
          setCatalog(types.items)
          setInstances(registered.items)
        }
      })
      .catch((failure: unknown) => {
        if (!cancelled) {
          setStatus({ intent: 'error', text: toApiError(failure).detail })
        }
      })
    return () => {
      cancelled = true
    }
  }, [open])

  function clearStatus(): void {
    setStatus(null)
  }

  function add(instance: ScorerInstance): void {
    setDraft((current) => {
      if (current.scorers.some((scorer) => scorer.scorer_id === instance.scorer_id)) return current
      return {
        ...current,
        primaryScorerId: current.primaryScorerId ?? instance.scorer_id,
        scorers: [...current.scorers, { ...instance, scope: 'response', highIsRisk: true }],
      }
    })
  }

  function buildSelectedParameters(): ReturnType<typeof buildScorerParameters> {
    if (!selected) {
      return { ok: false, error: 'Choose a scorer type.' }
    }
    if (blockedConfiguration) {
      return { ok: false, error: blockedConfiguration }
    }
    return buildScorerParameters(getScorerBuilderParameters(selected), values, { catalog })
  }

  async function validate(): Promise<void> {
    if (!selected) {
      return
    }
    const result = buildSelectedParameters()
    if (!result.ok) {
      setStatus({ intent: 'error', text: result.error })
      return
    }

    setBusy(true)
    clearStatus()
    try {
      const response = await scorersApi.validateScorer({ type, params: result.parameters ?? {} })
      setStatus(response.valid
        ? { intent: 'success', text: 'Configuration is valid.' }
        : { intent: 'warning', text: 'Configuration did not pass validation.' })
    } catch (failure: unknown) {
      setStatus({ intent: 'error', text: toApiError(failure).detail })
    } finally {
      setBusy(false)
    }
  }

  async function create(): Promise<void> {
    if (!selected) {
      return
    }
    const result = buildSelectedParameters()
    if (!result.ok) {
      setStatus({ intent: 'error', text: result.error })
      return
    }

    setBusy(true)
    clearStatus()
    try {
      const instance = await scorersApi.createScorer({ type, params: result.parameters ?? {} })
      setInstances([...instances.filter((item: ScorerInstance) => item.scorer_id !== instance.scorer_id), instance])
      add(instance)
      setType('')
      setValues({})
      setStatus({ intent: 'success', text: `${instance.scorer_type} is ready to use.` })
    } catch (failure: unknown) {
      setStatus({ intent: 'error', text: toApiError(failure).detail })
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(_, data) => {
      if (!data.open && !busy) {
        onClose()
      }
    }}
    >
      <DialogSurface className={styles.dialog}>
        <DialogBody>
          <DialogTitle>Workspace scoring</DialogTitle>
          <DialogContent className={styles.stack}>
            <Field label="Evaluation objective or rubric">
              <Textarea
                value={draft.objective}
                rows={3}
                onChange={(_, data) => {
                  setDraft({ ...draft, objective: data.value })
                }}
              />
            </Field>
            <Field label="Add registered scorer">
              <Select
                value=""
                disabled={busy}
                onChange={(_, data) => {
                  const instance = instances.find((item: ScorerInstance) => item.scorer_id === data.value)
                  if (instance) {
                    add(instance)
                  }
                }}
              >
                <option value="">Choose a registered scorer</option>
                {instances.map((instance: ScorerInstance) => (
                  <option key={instance.scorer_id} value={instance.scorer_id}>
                    {instance.scorer_type} ({instance.scorer_id})
                  </option>
                ))}
              </Select>
            </Field>
            {draft.scorers.map((scorer) => (
              <section className={styles.branchCard} key={scorer.scorer_id} aria-label={scorer.scorer_type}>
                <Text weight="semibold">{scorer.scorer_type}</Text>
                <Text className={styles.muted}>{scorer.score_type} · {scorer.identifier_hash.slice(0, 12)}</Text>
                <Field
                  label={`Evidence for ${scorer.scorer_type}`}
                  hint="All applicable messages invokes the scorer separately for each stored message; it is not a joint conversation verdict."
                >
                  <Select
                    value={scorer.scope}
                    onChange={(_, data) => {
                      if (data.value !== 'response' && data.value !== 'conversation') {
                        return
                      }
                      const scope = data.value
                      setDraft({
                        ...draft,
                        scorers: draft.scorers.map((item) => item.scorer_id === scorer.scorer_id ? { ...item, scope } : item),
                      })
                    }}
                  >
                    <option value="response">Current response</option>
                    <option value="conversation">All applicable messages</option>
                  </Select>
                </Field>
                <Checkbox
                  label="Higher values indicate more risk"
                  checked={scorer.highIsRisk}
                  onChange={(_, data) => {
                    setDraft({
                      ...draft,
                      scorers: draft.scorers.map((item) => item.scorer_id === scorer.scorer_id
                        ? { ...item, highIsRisk: data.checked === true }
                        : item),
                    })
                  }}
                />
                <Button
                  className={styles.button}
                  onClick={() => {
                    const remaining = draft.scorers.filter((item) => item.scorer_id !== scorer.scorer_id)
                    setDraft({
                      ...draft,
                      scorers: remaining,
                      primaryScorerId: draft.primaryScorerId === scorer.scorer_id ? remaining[0]?.scorer_id : draft.primaryScorerId,
                      autoScore: remaining.length > 0 && draft.autoScore,
                    })
                  }}
                >
                  Remove scorer
                </Button>
              </section>
            ))}
            <Field label="Primary node metric">
              <Select
                value={draft.primaryScorerId ?? ''}
                onChange={(_, data) => {
                  setDraft({ ...draft, primaryScorerId: data.value || undefined })
                }}
              >
                <option value="">First selected scorer</option>
                {draft.scorers.map((scorer) => (
                  <option key={scorer.scorer_id} value={scorer.scorer_id}>
                    {scorer.scorer_type}
                  </option>
                ))}
              </Select>
            </Field>
            <Checkbox
              label="Score automatically after each response"
              checked={draft.autoScore}
              disabled={draft.scorers.length === 0}
              onChange={(_, data) => {
                setDraft({ ...draft, autoScore: data.checked === true })
              }}
            />
            <details className={styles.disclosure}>
              <summary>Configure a new scorer</summary>
              <div className={styles.stack}>
                <Field label="Scorer type">
                  <Select
                    value={type}
                    disabled={busy}
                    onChange={(_, data) => {
                      setType(data.value)
                      const entry = catalog.find((item: ScorerCatalogEntry) => item.scorer_type === data.value)
                      setValues(getInitialScorerFormValues(entry, undefined, catalog, { prefillDefaults: false }))
                      clearStatus()
                    }}
                  >
                    <option value="">Choose a scorer type</option>
                    {catalog.map((entry: ScorerCatalogEntry) => (
                      <option key={entry.scorer_type} value={entry.scorer_type}>
                        {entry.scorer_type}{entry.is_llm_based ? ' (LLM)' : ''}
                      </option>
                    ))}
                  </Select>
                </Field>
                {selected && (
                  <section className={styles.branchCard} aria-label={`${selected.scorer_type} configuration`}>
                    <Text weight="semibold">{selected.scorer_type}</Text>
                    {selected.description && <Text className={styles.muted}>{selected.description}</Text>}
                    <ScorerBuilder
                      entry={selected}
                      catalog={catalog}
                      instances={instances}
                      targets={targets}
                      values={values}
                      disabled={busy}
                      onChange={(nextValues) => {
                        setValues(nextValues)
                        clearStatus()
                      }}
                    />
                  </section>
                )}
                {blockedConfiguration && (
                  <MessageBar intent="warning" layout="multiline">
                    <MessageBarBody>{blockedConfiguration}</MessageBarBody>
                  </MessageBar>
                )}
                <div className={styles.row}>
                  <Button
                    className={styles.button}
                    disabled={busy || !selected || blockedConfiguration != null}
                    onClick={() => {
                      void validate()
                    }}
                  >
                    Validate configuration
                  </Button>
                  <Button
                    className={styles.button}
                    disabled={busy || !selected || blockedConfiguration != null}
                    onClick={() => {
                      void create()
                    }}
                  >
                    Create and select scorer
                  </Button>
                </div>
              </div>
            </details>
            <Text className={styles.muted}>
              Scoring uses persisted backend evidence and may invoke a judge model. Per-message scopes and composite
              scorers can make multiple calls per scoring request. Values are verdicts, not confidence probabilities.
            </Text>
            {status && (
              <MessageBar intent={status.intent} layout="multiline">
                <MessageBarBody>{status.text}</MessageBarBody>
              </MessageBar>
            )}
          </DialogContent>
          <DialogActions>
            <Button className={styles.button} disabled={busy} onClick={onClose}>Cancel</Button>
            <Button
              className={styles.button}
              appearance="primary"
              disabled={busy}
              onClick={() => {
                void onSave(draft).then((saved: boolean) => {
                  if (saved) {
                    onClose()
                  }
                })
              }}
            >
              Save scoring settings
            </Button>
          </DialogActions>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
