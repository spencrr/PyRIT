import {
  buildParametersFromForm,
  getInitialFormValues,
  getParameterControlKind,
  type BuildParametersResult,
  type InitialFormValueOptions,
  type ParameterFormValue,
} from '@/components/Parameters/parameterForm'
import type { ScorerCatalogEntry, ScorerParameter } from '@/types'

export const SCORER_DEPTH_LIMIT = 6
export const SCORER_TOTAL_LIMIT = 32

export type StructuredScorerFormat = 'json' | 'text' | 'yaml'

export interface StructuredScorerValue {
  format: StructuredScorerFormat
  value: string
}

interface ReferenceFormValueBase {
  readonly id: string
}

export interface RegisteredReferenceFormValue extends ReferenceFormValueBase {
  readonly mode: 'registered'
  readonly alias: string
}

export interface InlineReferenceFormValue extends ReferenceFormValueBase {
  readonly mode: 'inline'
  readonly scorerType: string
  readonly values: Record<string, ScorerFormValue>
}

export type ReferenceFormValue = RegisteredReferenceFormValue | InlineReferenceFormValue

export type ScorerFormValue =
  | ParameterFormValue
  | StructuredScorerValue
  | ReferenceFormValue
  | ReferenceFormValue[]

export interface BuildScorerParametersOptions {
  catalog?: ScorerCatalogEntry[]
  depthLimit?: number
  totalLimit?: number
}

interface BuildContext {
  readonly catalog: ScorerCatalogEntry[]
  readonly depthLimit: number
  readonly totalLimit: number
  readonly totalConfigured: { count: number }
  readonly depth: number
}

interface NestedScorerPayload {
  type: string
  params?: Record<string, unknown> | null
}

let referenceValueCounter = 0

function nextReferenceValueId(): string {
  referenceValueCounter += 1
  return `scorer-reference-${referenceValueCounter}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isYamlWrapper(value: unknown): value is { yaml: string } {
  return isRecord(value) && typeof value.yaml === 'string'
}

function isNestedScorerPayload(value: unknown): value is NestedScorerPayload {
  return isRecord(value) && typeof value.type === 'string'
}

function isEmptyRegisteredReference(reference: ReferenceFormValue): boolean {
  return reference.mode === 'registered' && reference.alias.trim().length === 0
}

export function isStructuredScorerParameter(parameter: ScorerParameter): boolean {
  return parameter.reference_kind == null
    && parameter.input_kind !== 'unsupported'
    && (
      parameter.input_kind === 'json'
      || (parameter.accepts_text === true && (parameter.supports_yaml === true || parameter.json_schema != null))
    )
}

function isSimpleParameter(parameter: ScorerParameter): boolean {
  return parameter.reference_kind == null
    && !isStructuredScorerParameter(parameter)
    && parameter.input_kind !== 'unsupported'
}

function defaultStructuredFormat(parameter: ScorerParameter): StructuredScorerFormat {
  return parameter.accepts_text ? 'text' : 'json'
}

function createStructuredValue(
  parameter: ScorerParameter,
  source: unknown,
): StructuredScorerValue {
  if (typeof source === 'string') {
    return parameter.accepts_text
      ? { format: 'text', value: source }
      : { format: 'json', value: source }
  }
  if (isYamlWrapper(source) && parameter.supports_yaml) {
    return { format: 'yaml', value: source.yaml }
  }
  if (source !== undefined && source !== null) {
    return { format: 'json', value: JSON.stringify(source, null, 2) }
  }
  return { format: defaultStructuredFormat(parameter), value: '' }
}

function createReferenceValue(
  parameter: ScorerParameter,
  source: unknown,
  catalog: ScorerCatalogEntry[],
  options: InitialFormValueOptions,
): ReferenceFormValue {
  if (typeof source === 'string') {
    return createRegisteredReferenceValue(source)
  }
  if (parameter.reference_kind === 'scorer' && isNestedScorerPayload(source)) {
    const nestedEntry = catalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === source.type)
    return createInlineReferenceValue(
      source.type,
      getInitialScorerFormValues(nestedEntry, source.params ?? null, catalog, options),
    )
  }
  return createRegisteredReferenceValue('')
}

function extractSimpleParameterValue(value: ScorerFormValue | undefined): ParameterFormValue {
  if (typeof value === 'string') {
    return value
  }
  if (Array.isArray(value) && value.every((entry: string | ReferenceFormValue) => typeof entry === 'string')) {
    return value
  }
  return ''
}

function buildStructuredParameter(
  parameter: ScorerParameter,
  value: ScorerFormValue | undefined,
): { ok: true; value?: unknown } | { ok: false; error: string } {
  const structured = isStructuredScorerValue(value)
    ? value
    : typeof value === 'string'
      ? { format: defaultStructuredFormat(parameter), value }
      : { format: defaultStructuredFormat(parameter), value: '' }
  const raw = structured.value

  if (raw.trim().length === 0) {
    if (parameter.required) {
      return { ok: false, error: `${parameter.name} is required.` }
    }
    return { ok: true }
  }

  if (structured.format === 'text') {
    if (!parameter.accepts_text) {
      return { ok: false, error: `${parameter.name} does not accept plain text.` }
    }
    return { ok: true, value: raw }
  }

  if (structured.format === 'yaml') {
    if (!parameter.supports_yaml) {
      return { ok: false, error: `${parameter.name} does not accept YAML.` }
    }
    return { ok: true, value: { yaml: raw } }
  }

  let parsed: unknown
  try {
    parsed = JSON.parse(raw)
  } catch {
    return { ok: false, error: `${parameter.name} must be valid JSON.` }
  }
  if (parsed === null || typeof parsed !== 'object') {
    return { ok: false, error: `${parameter.name} must be a JSON object or array.` }
  }
  return { ok: true, value: parsed }
}

function buildReferenceValue(
  parameter: ScorerParameter,
  reference: ReferenceFormValue,
  context: BuildContext,
): { ok: true; value: string | NestedScorerPayload } | { ok: false; error: string } {
  if (reference.mode === 'registered') {
    const alias = reference.alias.trim()
    if (alias.length === 0) {
      return { ok: false, error: `${parameter.name} is required.` }
    }
    return { ok: true, value: alias }
  }

  if (parameter.reference_kind !== 'scorer') {
    return { ok: false, error: `${parameter.name} only supports registered references.` }
  }
  if (reference.scorerType.length === 0) {
    return { ok: false, error: `${parameter.name} scorer type is required.` }
  }

  const nestedEntry = context.catalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === reference.scorerType)
  if (!nestedEntry) {
    return { ok: false, error: `${reference.scorerType} is not available in the scorer catalog.` }
  }

  const nestedDepth = context.depth + 1
  if (nestedDepth > context.depthLimit) {
    return { ok: false, error: `Nested scorer depth cannot exceed ${context.depthLimit}.` }
  }

  context.totalConfigured.count += 1
  if (context.totalConfigured.count > context.totalLimit) {
    return { ok: false, error: `Nested scorer configuration cannot exceed ${context.totalLimit} scorers.` }
  }

  const nestedParameters = buildScorerParametersInternal(
    nestedEntry.parameters,
    reference.values,
    { ...context, depth: nestedDepth },
  )
  if (!nestedParameters.ok) {
    return nestedParameters
  }

  return {
    ok: true,
    value: {
      type: reference.scorerType,
      params: nestedParameters.parameters ?? {},
    },
  }
}

function buildScorerParametersInternal(
  parameters: ScorerParameter[],
  values: Record<string, ScorerFormValue>,
  context: BuildContext,
): BuildParametersResult {
  const unsupported = parameters.find((parameter: ScorerParameter) => parameter.input_kind === 'unsupported' && parameter.required)
  if (unsupported) {
    return { ok: false, error: `${unsupported.name} requires Python configuration. Register this scorer in an initializer instead.` }
  }

  const simpleParameters = parameters.filter(isSimpleParameter)
  const textParameters = simpleParameters.filter((parameter: ScorerParameter) => getParameterControlKind(parameter) === 'text')
  const simpleValues: Record<string, ParameterFormValue> = {}
  for (const parameter of simpleParameters) {
    simpleValues[parameter.name] = extractSimpleParameterValue(values[parameter.name])
  }

  const simpleResult = buildParametersFromForm(
    simpleParameters.filter((parameter: ScorerParameter) => getParameterControlKind(parameter) !== 'text'),
    simpleValues,
  )
  if (!simpleResult.ok) {
    return simpleResult
  }

  const output: Record<string, unknown> = { ...(simpleResult.parameters ?? {}) }

  // Regexes, prompts, and other free text are evidence, not identifiers.
  for (const parameter of textParameters) {
    const raw = simpleValues[parameter.name]
    if (typeof raw === 'string' && raw.length > 0) {
      output[parameter.name] = raw
    } else if (parameter.required) {
      return { ok: false, error: `${parameter.name} is required.` }
    }
  }

  for (const parameter of parameters) {
    if (parameter.reference_kind != null) {
      const raw = values[parameter.name]
      if (parameter.is_list) {
        const references = isReferenceFormValueList(raw) ? raw : []
        if (references.length === 0) {
          if (parameter.required) {
            return { ok: false, error: `${parameter.name} is required.` }
          }
          continue
        }
        const built: Array<string | NestedScorerPayload> = []
        for (const reference of references) {
          const result = buildReferenceValue(parameter, reference, context)
          if (!result.ok) {
            return result
          }
          built.push(result.value)
        }
        output[parameter.name] = built
        continue
      }

      if (!isReferenceFormValue(raw)) {
        if (parameter.required) {
          return { ok: false, error: `${parameter.name} is required.` }
        }
        continue
      }
      if (!parameter.required && isEmptyRegisteredReference(raw)) {
        continue
      }
      const result = buildReferenceValue(parameter, raw, context)
      if (!result.ok) {
        return result
      }
      output[parameter.name] = result.value
      continue
    }

    if (isStructuredScorerParameter(parameter)) {
      const result = buildStructuredParameter(parameter, values[parameter.name])
      if (!result.ok) {
        return result
      }
      if (result.value !== undefined) {
        output[parameter.name] = result.value
      }
    }
  }

  return { ok: true, parameters: output }
}

function findUnsupportedConfigurationInternal(
  parameters: ScorerParameter[],
  values: Record<string, ScorerFormValue>,
  catalog: ScorerCatalogEntry[],
  depth: number,
): string | null {
  if (depth > SCORER_DEPTH_LIMIT) {
    return `Nested scorer depth cannot exceed ${SCORER_DEPTH_LIMIT}.`
  }

  const unsupported = parameters.find((parameter: ScorerParameter) => parameter.input_kind === 'unsupported' && parameter.required)
  if (unsupported) {
    return `${unsupported.name} requires Python configuration. Register this scorer in an initializer instead.`
  }

  for (const parameter of parameters) {
    if (parameter.reference_kind !== 'scorer') {
      continue
    }
    const raw = values[parameter.name]
    const references = parameter.is_list
      ? (isReferenceFormValueList(raw) ? raw : [])
      : isReferenceFormValue(raw)
        ? [raw]
        : []
    for (const reference of references) {
      if (reference.mode !== 'inline' || reference.scorerType.length === 0) {
        continue
      }
      const nestedEntry = catalog.find((entry: ScorerCatalogEntry) => entry.scorer_type === reference.scorerType)
      if (!nestedEntry) {
        continue
      }
      const nestedReason = findUnsupportedConfigurationInternal(
        nestedEntry.parameters,
        reference.values,
        catalog,
        depth + 1,
      )
      if (nestedReason) {
        return nestedReason
      }
    }
  }

  return null
}

export function createRegisteredReferenceValue(alias = ''): RegisteredReferenceFormValue {
  return {
    id: nextReferenceValueId(),
    mode: 'registered',
    alias,
  }
}

export function createInlineReferenceValue(
  scorerType = '',
  values: Record<string, ScorerFormValue> = {},
): InlineReferenceFormValue {
  return {
    id: nextReferenceValueId(),
    mode: 'inline',
    scorerType,
    values,
  }
}

export function isStructuredScorerValue(value: ScorerFormValue | undefined): value is StructuredScorerValue {
  return isRecord(value) && typeof value.format === 'string' && typeof value.value === 'string'
}

export function isReferenceFormValue(value: unknown): value is ReferenceFormValue {
  return isRecord(value) && typeof value.id === 'string' && typeof value.mode === 'string'
}

export function isReferenceFormValueList(value: unknown): value is ReferenceFormValue[] {
  return Array.isArray(value) && value.every((entry: unknown) => isReferenceFormValue(entry))
}

export function getInitialScorerFormValues(
  entry: ScorerCatalogEntry | undefined,
  initialParameters?: Record<string, unknown> | null,
  catalog: ScorerCatalogEntry[] = [],
  options: InitialFormValueOptions = {},
): Record<string, ScorerFormValue> {
  if (!entry) {
    return {}
  }

  const parameters = entry.parameters
  const values: Record<string, ScorerFormValue> = {}
  const simpleParameters = parameters.filter(isSimpleParameter)
  const initialSimpleValues = getInitialFormValues(simpleParameters, initialParameters, options)

  for (const parameter of simpleParameters) {
    values[parameter.name] = initialSimpleValues[parameter.name] ?? ''
  }

  for (const parameter of parameters) {
    const hasInitialValue = initialParameters != null && Object.prototype.hasOwnProperty.call(initialParameters, parameter.name)
    const source = hasInitialValue
      ? initialParameters?.[parameter.name]
      : options.prefillDefaults ?? true
        ? parameter.default
        : undefined

    if (parameter.reference_kind != null) {
      if (parameter.is_list) {
        values[parameter.name] = Array.isArray(source)
          ? source.map((entryValue: unknown) => createReferenceValue(parameter, entryValue, catalog, options))
          : []
        continue
      }
      values[parameter.name] = createReferenceValue(parameter, source, catalog, options)
      continue
    }

    if (isStructuredScorerParameter(parameter)) {
      values[parameter.name] = createStructuredValue(parameter, source)
    }
  }

  return values
}

export function createPresetValue(
  parameter: ScorerParameter,
  preset: unknown,
  catalog: ScorerCatalogEntry[],
  currentValue?: ScorerFormValue,
): ScorerFormValue {
  if (parameter.reference_kind != null) {
    if (parameter.is_list) {
      return Array.isArray(preset)
        ? preset.map((entry: unknown) => createReferenceValue(parameter, entry, catalog, { prefillDefaults: false }))
        : []
    }
    return createReferenceValue(parameter, preset, catalog, { prefillDefaults: false })
  }

  if (isStructuredScorerParameter(parameter)) {
    if (isStructuredScorerValue(currentValue) && typeof preset === 'string') {
      return { format: currentValue.format, value: preset }
    }
    return createStructuredValue(parameter, preset)
  }

  if (parameter.is_list) {
    return Array.isArray(preset) ? preset.map((entry: unknown) => String(entry)).join(', ') : String(preset ?? '')
  }

  if (typeof preset === 'boolean') {
    return preset ? 'true' : 'false'
  }

  return preset != null ? String(preset) : ''
}

export function buildScorerParameters(
  parameters: ScorerParameter[],
  values: Record<string, ScorerFormValue>,
  options: BuildScorerParametersOptions = {},
): BuildParametersResult {
  return buildScorerParametersInternal(parameters, values, {
    catalog: options.catalog ?? [],
    depthLimit: options.depthLimit ?? SCORER_DEPTH_LIMIT,
    totalLimit: options.totalLimit ?? SCORER_TOTAL_LIMIT,
    totalConfigured: { count: 1 },
    depth: 1,
  })
}

export function findUnsupportedScorerConfiguration(
  entry: ScorerCatalogEntry | undefined,
  values: Record<string, ScorerFormValue>,
  catalog: ScorerCatalogEntry[],
): string | null {
  if (!entry) {
    return null
  }
  return findUnsupportedConfigurationInternal(entry.parameters, values, catalog, 1)
}
