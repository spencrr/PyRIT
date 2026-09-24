import {
  Button,
  Field,
  MessageBar,
  MessageBarBody,
  Select,
  Text,
  Textarea,
} from '@fluentui/react-components'

import ParameterField from '@/components/Parameters/ParameterField'
import type { ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { ScorerCatalogEntry, ScorerInstance, ScorerParameter, TargetInstance } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'
import { useScorerBuilderStyles } from './ScorerBuilder.styles'
import {
  SCORER_DEPTH_LIMIT,
  createInlineReferenceValue,
  createPresetValue,
  createRegisteredReferenceValue,
  getInitialScorerFormValues,
  isReferenceFormValue,
  isReferenceFormValueList,
  isStructuredScorerParameter,
  isStructuredScorerValue,
  type ReferenceFormValue,
  type ScorerFormValue,
  type StructuredScorerValue,
} from './scorerForm'

interface ScorerBuilderProps {
  entry: ScorerCatalogEntry
  catalog: ScorerCatalogEntry[]
  instances: ScorerInstance[]
  targets: TargetInstance[]
  values: Record<string, ScorerFormValue>
  disabled: boolean
  depth?: number
  onChange: (values: Record<string, ScorerFormValue>) => void
}

interface ParameterActionsProps {
  parameter: ScorerParameter
  catalog: ScorerCatalogEntry[]
  currentValue?: ScorerFormValue
  disabled: boolean
  onApply: (value: ScorerFormValue) => void
}

interface ReferenceEditorProps {
  parameter: ScorerParameter
  catalog: ScorerCatalogEntry[]
  instances: ScorerInstance[]
  targets: TargetInstance[]
  value: ScorerFormValue | undefined
  disabled: boolean
  depth: number
  onChange: (value: ScorerFormValue) => void
}

interface ReferenceItemEditorProps {
  parameter: ScorerParameter
  catalog: ScorerCatalogEntry[]
  instances: ScorerInstance[]
  targets: TargetInstance[]
  reference: ReferenceFormValue
  disabled: boolean
  depth: number
  onChange: (value: ReferenceFormValue) => void
}

function parameterHint(parameter: ScorerParameter, kind: 'scorer' | 'target'): string | undefined {
  const parts: string[] = []
  if (parameter.description) {
    parts.push(parameter.description)
  }
  const acceptedTypeCount = parameter.accepted_types?.length ?? 0
  if (acceptedTypeCount > 0) {
    if (acceptedTypeCount <= 3) {
      parts.push(`Compatible ${kind} types: ${parameter.accepted_types?.join(', ')}.`)
    } else {
      parts.push(`${acceptedTypeCount} compatible ${kind} types (filtered below).`)
    }
  }
  return parts.length > 0 ? parts.join(' ') : undefined
}

function parameterLabel(parameter: ScorerParameter): string {
  return parameter.reference_kind === 'target' && parameter.name === 'chat_target'
    ? 'Judge target'
    : parameter.name
}

function textValue(value: ScorerFormValue | undefined): string {
  return typeof value === 'string' ? value : ''
}

function structuredValue(parameter: ScorerParameter, value: ScorerFormValue | undefined): StructuredScorerValue {
  if (isStructuredScorerValue(value)) {
    return value
  }
  return { format: parameter.accepts_text ? 'text' : 'json', value: '' }
}

function renderReferenceSummary(reference: ReferenceFormValue): string {
  if (reference.mode === 'registered') {
    return reference.alias.trim().length > 0 ? reference.alias : 'Choose a registered component'
  }
  return reference.scorerType.length > 0 ? reference.scorerType : 'Choose a scorer type'
}

function compatibleScorerCatalog(
  parameter: ScorerParameter,
  catalog: ScorerCatalogEntry[],
): ScorerCatalogEntry[] {
  return catalog.filter((entry: ScorerCatalogEntry) => parameter.accepted_types?.includes(entry.scorer_type) === true)
}

function compatibleInstances(
  parameter: ScorerParameter,
  instances: ScorerInstance[],
): ScorerInstance[] {
  return instances.filter((instance: ScorerInstance) => parameter.choices?.includes(instance.scorer_id) === true)
}

function compatibleTargets(
  parameter: ScorerParameter,
  targets: TargetInstance[],
): TargetInstance[] {
  return targets.filter((target: TargetInstance) => parameter.choices?.includes(target.target_registry_name) === true)
}

function ParameterActions({ parameter, catalog, currentValue, disabled, onApply }: ParameterActionsProps) {
  const styles = useScorerBuilderStyles()
  const actions: Array<{ key: string; label: string; value: unknown }> = []
  if (parameter.example) {
    actions.push({ key: 'example', label: `Use example ${parameter.name}`, value: parameter.example })
  }
  for (const preset of parameter.presets ?? []) {
    actions.push({ key: `preset-${preset.name}`, label: `Use ${preset.name}`, value: preset.value })
  }
  if (actions.length === 0) {
    return null
  }
  return (
    <div className={styles.nestedActions}>
      {actions.map((action) => (
        <Button
          key={action.key}
          className={styles.button}
          disabled={disabled}
          onClick={() => {
            onApply(createPresetValue(parameter, action.value, catalog, currentValue))
          }}
        >
          {action.label}
        </Button>
      ))}
    </div>
  )
}

function ReferenceItemEditor({
  parameter,
  catalog,
  instances,
  targets,
  reference,
  disabled,
  depth,
  onChange,
}: ReferenceItemEditorProps) {
  const treeStyles = useConversationTreeStyles()
  const styles = useScorerBuilderStyles()
  const scorerCatalog = compatibleScorerCatalog(parameter, catalog)
  const scorerInstances = compatibleInstances(parameter, instances)
  const targetChoices = compatibleTargets(parameter, targets)
  const supportsInline = parameter.reference_kind === 'scorer' && parameter.accepts_inline === true
  const isAtDepthLimit = depth >= SCORER_DEPTH_LIMIT
  const registeredHint = parameterHint(parameter, parameter.reference_kind === 'target' ? 'target' : 'scorer')
  const label = parameterLabel(parameter)

  const inlineReference = reference.mode === 'inline'
    ? reference
    : createInlineReferenceValue(
      scorerCatalog[0]?.scorer_type ?? '',
      getInitialScorerFormValues(scorerCatalog[0], undefined, catalog, { prefillDefaults: false }),
    )
  const nestedEntry = scorerCatalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === inlineReference.scorerType)

  return (
    <div className={styles.nestedBody}>
      {supportsInline && (
        <Field label={`${label} source`}>
          <Select
            value={reference.mode}
            disabled={disabled}
            onChange={(_, data) => {
              if (data.value === 'inline' && !isAtDepthLimit) {
                onChange(inlineReference)
                return
              }
              onChange(createRegisteredReferenceValue(reference.mode === 'registered' ? reference.alias : ''))
            }}
          >
            <option value="registered">Use registered scorer</option>
            <option value="inline" disabled={isAtDepthLimit}>Configure nested scorer</option>
          </Select>
        </Field>
      )}
      {(reference.mode === 'registered' || !supportsInline) && (
        <Field label={label} required={parameter.required} hint={registeredHint}>
          <Select
            value={reference.mode === 'registered' ? reference.alias : ''}
            disabled={disabled}
            onChange={(_, data) => {
              onChange({ ...createRegisteredReferenceValue(data.value), id: reference.id })
            }}
          >
            <option value="">{parameter.reference_kind === 'target' ? 'Choose a registered target' : 'Choose a registered scorer'}</option>
            {reference.mode === 'registered'
              && reference.alias.length > 0
              && !scorerInstances.some((instance: ScorerInstance) => instance.scorer_id === reference.alias)
              && !targetChoices.some((target: TargetInstance) => target.target_registry_name === reference.alias)
              && (
                <option value={reference.alias}>
                  {reference.alias} (unavailable)
                </option>
              )}
            {parameter.reference_kind === 'target'
              ? targetChoices.map((target: TargetInstance) => (
                <option key={target.target_registry_name} value={target.target_registry_name}>
                  {target.target_registry_name} ({target.identifier.class_name})
                </option>
              ))
              : scorerInstances.map((instance: ScorerInstance) => (
                <option key={instance.scorer_id} value={instance.scorer_id}>
                  {instance.scorer_type} ({instance.scorer_id})
                </option>
              ))}
          </Select>
        </Field>
      )}
      {reference.mode === 'inline' && supportsInline && (
        <section className={styles.nestedCard} aria-label={`${parameter.name} nested scorer`}>
          <Field
            label={`${label} scorer type`}
            required={parameter.required}
            hint={parameterHint(parameter, 'scorer')}
          >
            <Select
              value={inlineReference.scorerType}
              disabled={disabled}
              onChange={(_, data) => {
                const nextEntry = scorerCatalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === data.value)
                onChange(createInlineReferenceValue(
                  data.value,
                  getInitialScorerFormValues(nextEntry, undefined, catalog, { prefillDefaults: false }),
                ))
              }}
            >
              <option value="">Choose a scorer type</option>
              {inlineReference.scorerType.length > 0
                && !scorerCatalog.some((entry: ScorerCatalogEntry) => entry.scorer_type === inlineReference.scorerType)
                && <option value={inlineReference.scorerType}>{inlineReference.scorerType} (unavailable)</option>}
              {scorerCatalog.map((entry: ScorerCatalogEntry) => (
                <option key={entry.scorer_type} value={entry.scorer_type}>
                  {entry.scorer_type}{entry.is_llm_based ? ' (LLM)' : ''}
                </option>
              ))}
            </Select>
          </Field>
          {isAtDepthLimit && (
            <MessageBar intent="warning" layout="multiline">
              <MessageBarBody>Nested scorer depth cannot exceed {SCORER_DEPTH_LIMIT}.</MessageBarBody>
            </MessageBar>
          )}
          {nestedEntry
            ? <>
              <Text weight="semibold">{nestedEntry.scorer_type}</Text>
              {nestedEntry.description && <Text className={treeStyles.muted}>{nestedEntry.description}</Text>}
              <ScorerBuilder
                entry={nestedEntry}
                catalog={catalog}
                instances={instances}
                targets={targets}
                values={inlineReference.values}
                disabled={disabled}
                depth={depth + 1}
                onChange={(nextValues) => {
                  onChange({ ...inlineReference, values: nextValues })
                }}
              />
            </>
            : <Text className={treeStyles.muted}>Choose a scorer type to configure nested parameters.</Text>}
        </section>
      )}
    </div>
  )
}

function ReferenceEditor({
  parameter,
  catalog,
  instances,
  targets,
  value,
  disabled,
  depth,
  onChange,
}: ReferenceEditorProps) {
  const treeStyles = useConversationTreeStyles()
  const styles = useScorerBuilderStyles()

  if (parameter.is_list) {
    const references = isReferenceFormValueList(value) ? value : []
    return (
      <Field
        label={parameterLabel(parameter)}
        required={parameter.required}
        hint={parameterHint(parameter, parameter.reference_kind === 'target' ? 'target' : 'scorer')}
      >
        <div className={styles.nestedList}>
          {references.length === 0 && <Text className={treeStyles.muted}>No configured references yet.</Text>}
          {references.map((reference: ReferenceFormValue, index: number) => (
            <section className={styles.nestedCard} key={reference.id} aria-label={`${parameter.name} ${index + 1}`}>
              <div className={styles.nestedHeader}>
                <Text weight="semibold">{parameter.name} {index + 1}</Text>
                <Text className={treeStyles.muted}>{renderReferenceSummary(reference)}</Text>
              </div>
              <ReferenceItemEditor
                parameter={parameter}
                catalog={catalog}
                instances={instances}
                targets={targets}
                reference={reference}
                disabled={disabled}
                depth={depth}
                onChange={(nextReference) => {
                  const next = references.map((item: ReferenceFormValue) => item.id === reference.id ? nextReference : item)
                  onChange(next)
                }}
              />
              <div className={styles.nestedActions}>
                <Button
                  className={styles.button}
                  disabled={disabled || index === 0}
                  onClick={() => {
                    const next = [...references]
                    ;[next[index - 1], next[index]] = [next[index], next[index - 1]]
                    onChange(next)
                  }}
                >
                  Move up
                </Button>
                <Button
                  className={styles.button}
                  disabled={disabled || index === references.length - 1}
                  onClick={() => {
                    const next = [...references]
                    ;[next[index], next[index + 1]] = [next[index + 1], next[index]]
                    onChange(next)
                  }}
                >
                  Move down
                </Button>
                <Button
                  className={styles.button}
                  disabled={disabled}
                  onClick={() => {
                    onChange(references.filter((item: ReferenceFormValue) => item.id !== reference.id))
                  }}
                >
                  Remove
                </Button>
              </div>
            </section>
          ))}
          <Button
            className={styles.button}
            disabled={disabled}
            onClick={() => {
              onChange([...references, createRegisteredReferenceValue('')])
            }}
          >
            Add {parameter.reference_kind === 'target' ? 'target' : 'scorer'}
          </Button>
        </div>
      </Field>
    )
  }

  const reference = isReferenceFormValue(value) ? value : createRegisteredReferenceValue('')

  return (
    <ReferenceItemEditor
      parameter={parameter}
      catalog={catalog}
      instances={instances}
      targets={targets}
      reference={reference}
      disabled={disabled}
      depth={depth}
      onChange={onChange}
    />
  )
}

export default function ScorerBuilder({
  entry,
  catalog,
  instances,
  targets,
  values,
  disabled,
  depth = 1,
  onChange,
}: ScorerBuilderProps) {
  const treeStyles = useConversationTreeStyles()
  const styles = useScorerBuilderStyles()
  const parameters = entry.parameters

  function updateValue(name: string, nextValue: ScorerFormValue): void {
    onChange({ ...values, [name]: nextValue })
  }

  return (
    <div className={styles.builder}>
      {parameters.map((parameter: ScorerParameter) => {
        if (parameter.input_kind === 'unsupported') {
          return parameter.required
            ? (
              <MessageBar key={parameter.name} intent="warning" layout="multiline">
                <MessageBarBody>
                  {parameter.name} requires Python-only configuration. Register this scorer in an initializer, then select the registered scorer instead.
                </MessageBarBody>
              </MessageBar>
            )
            : null
        }

        if (parameter.reference_kind != null) {
          return (
            <ReferenceEditor
              key={parameter.name}
              parameter={parameter}
              catalog={catalog}
              instances={instances}
              targets={targets}
              value={values[parameter.name]}
              disabled={disabled}
              depth={depth}
              onChange={(nextValue) => {
                updateValue(parameter.name, nextValue)
              }}
            />
          )
        }

        if (isStructuredScorerParameter(parameter)) {
          const current = structuredValue(parameter, values[parameter.name])
          const formatHint = [parameter.description, `${parameter.type_name} as structured data.`].filter(Boolean).join(' ')
          return (
            <div className={styles.fieldStack} key={parameter.name}>
              {(parameter.accepts_text || parameter.supports_yaml) && (
                <Field label={`${parameter.name} format`}>
                  <Select
                    value={current.format}
                    disabled={disabled}
                    onChange={(_, data) => {
                      if (data.value !== 'json' && data.value !== 'text' && data.value !== 'yaml') {
                        return
                      }
                      updateValue(parameter.name, { ...current, format: data.value })
                    }}
                  >
                    {parameter.accepts_text && <option value="text">Text</option>}
                    <option value="json">JSON</option>
                    {parameter.supports_yaml && <option value="yaml">YAML</option>}
                  </Select>
                </Field>
              )}
              <Field label={parameter.name} required={parameter.required} hint={formatHint}>
                <Textarea
                  className={styles.textarea}
                  rows={current.format === 'json' ? 8 : 5}
                  disabled={disabled}
                  value={current.value}
                  onChange={(_, data) => {
                    updateValue(parameter.name, { ...current, value: data.value })
                  }}
                />
              </Field>
              <ParameterActions
                parameter={parameter}
                catalog={catalog}
                currentValue={values[parameter.name]}
                disabled={disabled}
                onApply={(nextValue) => {
                  updateValue(parameter.name, nextValue)
                }}
              />
              {parameter.json_schema && (
                <details className={treeStyles.disclosure}>
                  <summary>{parameter.name} schema</summary>
                  <pre className={treeStyles.parameters}>{JSON.stringify(parameter.json_schema, null, 2)}</pre>
                </details>
              )}
            </div>
          )
        }

        if (parameter.input_kind === 'multiline') {
          return (
            <div className={styles.fieldStack} key={parameter.name}>
              <Field label={parameter.name} required={parameter.required} hint={parameter.description ?? undefined}>
                <Textarea
                  rows={4}
                  disabled={disabled}
                  value={textValue(values[parameter.name])}
                  onChange={(_, data) => {
                    updateValue(parameter.name, data.value)
                  }}
                />
              </Field>
              <ParameterActions
                parameter={parameter}
                catalog={catalog}
                currentValue={values[parameter.name]}
                disabled={disabled}
                onApply={(nextValue) => {
                  updateValue(parameter.name, nextValue)
                }}
              />
            </div>
          )
        }

        return (
          <div className={styles.fieldStack} key={parameter.name}>
            <ParameterField
              parameter={parameter}
              value={extractParameterFormValue(values[parameter.name])}
              disabled={disabled}
              onChange={(name: string, nextValue: ParameterFormValue) => {
                updateValue(name, nextValue)
              }}
            />
            <ParameterActions
              parameter={parameter}
              catalog={catalog}
              currentValue={values[parameter.name]}
              disabled={disabled}
              onApply={(nextValue) => {
                updateValue(parameter.name, nextValue)
              }}
            />
          </div>
        )
      })}
    </div>
  )
}

function extractParameterFormValue(value: ScorerFormValue | undefined): ParameterFormValue {
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value) && value.every((entry: string | ReferenceFormValue) => typeof entry === 'string')) {
    return value
  }
  return ''
}
