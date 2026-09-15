import { buildParametersFromForm, type BuildParametersResult, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import type { ScorerParameter } from '@/types'

export function buildScorerParameters(parameters: ScorerParameter[], values: Record<string, ParameterFormValue>): BuildParametersResult {
  const unsupported = parameters.find((parameter) => parameter.input_kind === 'unsupported' && parameter.required)
  if (unsupported) return { ok: false, error: `${unsupported.name} requires Python configuration. Register this scorer in an initializer instead.` }
  const fields = parameters.filter((parameter) => parameter.input_kind !== 'json' && parameter.input_kind !== 'unsupported')
  const result = buildParametersFromForm(fields, values)
  if (!result.ok) return result
  const output = { ...result.parameters }
  for (const parameter of parameters.filter((item) => item.input_kind === 'json')) {
    const raw = values[parameter.name]
    if (typeof raw !== 'string' || !raw.trim()) {
      if (parameter.required) return { ok: false, error: `${parameter.name} is required.` }
      continue
    }
    let value: unknown
    try { value = JSON.parse(raw) }
    catch { return { ok: false, error: `${parameter.name} must be valid JSON.` } }
    if (value === null || typeof value !== 'object' || Array.isArray(value)) {
      return { ok: false, error: `${parameter.name} must be a JSON object.` }
    }
    output[parameter.name] = value
  }
  return { ok: true, parameters: output }
}
