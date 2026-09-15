import { useState } from 'react'

import { Button, Field, MessageBar, MessageBarBody, Select, Text } from '@fluentui/react-components'
import { AddRegular, ArrowDownRegular, ArrowUpRegular, DeleteRegular } from '@fluentui/react-icons'

import ParameterField from '@/components/Parameters/ParameterField'
import {
  buildParametersFromForm, getInitialFormValues, type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ConverterCatalogEntry, TargetInstance, TreeConverterSpec } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'

interface ConverterFormProps {
  catalog: ConverterCatalogEntry[]
  targets: TargetInstance[]
  value?: TreeConverterSpec
  disabled: boolean
  onSave: (converter: TreeConverterSpec) => void
  onCancel: () => void
  onDirtyChange: (dirty: boolean) => void
}

function ConverterForm({ catalog, targets, value, disabled, onSave, onCancel, onDirtyChange }: ConverterFormProps) {
  const styles = useConversationTreeStyles()
  const [type, setType] = useState(value?.type ?? '')
  const selected = catalog.find((entry: ConverterCatalogEntry) => entry.converter_type === type)
  const [values, setValues] = useState<Record<string, ParameterFormValue>>(() =>
    getInitialFormValues(selected?.parameters ?? [], value?.params, { prefillDefaults: false }))
  const [rewriteTarget, setRewriteTarget] = useState(
    typeof value?.params.converter_target === 'string' ? value.params.converter_target : '')
  const [error, setError] = useState('')
  const textConverters = catalog.filter((entry: ConverterCatalogEntry) =>
    entry.supported_input_types.includes('text') && entry.supported_output_types.length > 0
    && entry.supported_output_types.every((dataType: string) => dataType === 'text'))

  function save(): void {
    if (!selected) return
    const result = buildParametersFromForm(selected.parameters, values)
    if (!result.ok) {
      setError(result.error)
      return
    }
    // Preserve imported parameters not exposed by the scalar-only catalog.
    const retained = type === value?.type
      ? Object.fromEntries(Object.entries(value.params).filter(([key]: [string, unknown]) =>
        key !== 'converter_target' && !selected.parameters.some((parameter) => parameter.name === key)))
      : {}
    onSave({
      type,
      params: {
        ...retained,
        ...result.parameters,
        ...(selected.is_llm_based && rewriteTarget ? { converter_target: rewriteTarget } : {}),
      },
    })
  }

  return (
    <div className={styles.stack}>
      <Field label="Converter">
        <Select value={type} disabled={disabled} onChange={(_, data) => {
          setType(data.value)
          const entry = catalog.find((converter: ConverterCatalogEntry) => converter.converter_type === data.value)
          setValues(getInitialFormValues(entry?.parameters ?? [], undefined, { prefillDefaults: false }))
          setRewriteTarget('')
          setError('')
          onDirtyChange(true)
        }}>
          <option value="">Choose a converter</option>
          {textConverters.map((entry: ConverterCatalogEntry) => (
            <option key={entry.converter_type} value={entry.converter_type}>
              {entry.converter_type}{entry.is_llm_based ? ' (LLM rewrite)' : ''}
            </option>
          ))}
        </Select>
      </Field>
      {selected?.is_llm_based && (
        <Field label="Rewrite target">
          <Select value={rewriteTarget} disabled={disabled} onChange={(_, data) => {
            setRewriteTarget(data.value)
            onDirtyChange(true)
          }}>
            <option value="">Backend default</option>
            {rewriteTarget && !targets.some((target: TargetInstance) => target.target_registry_name === rewriteTarget)
              && <option value={rewriteTarget}>{rewriteTarget} (unavailable)</option>}
            {targets.filter((target: TargetInstance) => target.capabilities?.supports_multi_turn
              && target.capabilities.supports_editable_history
              && target.capabilities.supported_input_modalities.includes('text'))
              .map((target: TargetInstance) => <option key={target.target_registry_name} value={target.target_registry_name}>{target.target_registry_name}</option>)}
          </Select>
        </Field>
      )}
      {selected?.parameters.map((parameter) => (
        <ParameterField key={parameter.name} parameter={parameter} value={values[parameter.name] ?? ''}
          disabled={disabled} onChange={(name: string, next: ParameterFormValue) => {
            setValues((previous) => ({ ...previous, [name]: next }))
            onDirtyChange(true)
          }} />
      ))}
      {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
      <div className={styles.row}>
        <Button className={styles.button} appearance="primary" disabled={disabled || !selected} onClick={save}>
          {value ? 'Apply converter changes' : 'Add converter'}
        </Button>
        <Button className={styles.button} disabled={disabled} onClick={onCancel}>Cancel converter</Button>
      </div>
    </div>
  )
}

interface TreePipelineEditorProps {
  converters: TreeConverterSpec[]
  catalog: ConverterCatalogEntry[]
  targets: TargetInstance[]
  disabled: boolean
  onChange: (converters: TreeConverterSpec[]) => void
  onEditingChange?: (editing: boolean) => void
}

/** Same editable pipeline in the prompt inspector and each independent comparison branch. */
export default function TreePipelineEditor({
  converters, catalog, targets, disabled, onChange, onEditingChange,
}: TreePipelineEditorProps) {
  const styles = useConversationTreeStyles()
  const [editing, setEditing] = useState<number | 'new' | null>(null)

  function close(): void {
    setEditing(null)
    onEditingChange?.(false)
  }

  function move(index: number, direction: number): void {
    const next = [...converters]
    const target = index + direction
    ;[next[index], next[target]] = [next[target], next[index]]
    onChange(next)
  }

  return (
    <div className={styles.stack}>
      {converters.length === 0 && <Text className={styles.muted}>No converters</Text>}
      {converters.map((converter: TreeConverterSpec, index: number) => (
        <div className={styles.pipelineStep} key={`${index}-${converter.type}`}>
          <Text weight="semibold">{index + 1}. {converter.type}</Text>
          {Object.keys(converter.params).length > 0 && <pre className={styles.parameters}>{JSON.stringify(converter.params, null, 2)}</pre>}
          <div className={styles.row}>
            <Button className={styles.button} disabled={disabled || editing !== null} onClick={() => {
              setEditing(index)
              onEditingChange?.(true)
            }} aria-label={`Edit converter ${index + 1}`}>Edit</Button>
            <Button className={styles.button} disabled={disabled || editing !== null || index === 0}
              icon={<ArrowUpRegular />} aria-label={`Move converter ${index + 1} up`} onClick={() => { move(index, -1) }} />
            <Button className={styles.button} disabled={disabled || editing !== null || index === converters.length - 1}
              icon={<ArrowDownRegular />} aria-label={`Move converter ${index + 1} down`} onClick={() => { move(index, 1) }} />
            <Button className={styles.button} disabled={disabled || editing !== null}
              icon={<DeleteRegular />} aria-label={`Remove converter ${index + 1}`}
              onClick={() => { onChange(converters.filter((_, position: number) => position !== index)) }} />
          </div>
        </div>
      ))}
      {editing === null
        ? <Button className={styles.button} icon={<AddRegular />} disabled={disabled} onClick={() => {
          setEditing('new')
          onEditingChange?.(true)
        }}>Add converter step</Button>
        : <ConverterForm
          key={editing}
          catalog={catalog}
          targets={targets}
          value={typeof editing === 'number' ? converters[editing] : undefined}
          disabled={disabled}
          onDirtyChange={onEditingChange ?? (() => undefined)}
          onCancel={close}
          onSave={(converter: TreeConverterSpec) => {
            onChange(editing === 'new' ? [...converters, converter]
              : converters.map((previous: TreeConverterSpec, index: number) => index === editing ? converter : previous))
            close()
          }}
        />}
    </div>
  )
}
