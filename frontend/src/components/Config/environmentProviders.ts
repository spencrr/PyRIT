import type { EnvironmentProviderInfo, Parameter } from '@/types'

// Compatibility with backends predating provider metadata; an explicit empty catalog stays empty.
export const BUILTIN_ENVIRONMENT_PROVIDERS: readonly EnvironmentProviderInfo[] = [
  { name: 'docker', display_name: 'Docker container', capabilities: { isolated: true, checkpoint: true } },
  { name: 'local', display_name: 'Local process (not sandboxed)', capabilities: { isolated: false, checkpoint: false } },
]

export const FIRST_PARTY_ENVIRONMENTS: ReadonlySet<string> = new Set(['docker', 'local'])

interface SchemaProperty {
  readonly type?: string | string[]
  readonly description?: string
  readonly default?: unknown
  readonly enum?: unknown[]
}

const SCHEMA_TYPE_NAMES: Record<string, string> = {
  string: 'str',
  integer: 'int',
  number: 'float',
  boolean: 'bool',
}

function schemaType(property: SchemaProperty): string | null {
  const declared = Array.isArray(property.type)
    ? property.type.find((candidate) => candidate !== 'null')
    : property.type
  return declared ? SCHEMA_TYPE_NAMES[declared] ?? null : null
}

/**
 * Project a provider's flat JSON-schema settings into the shared scalar parameter contract.
 * Nested objects are not editable here; providers needing them register templates from Python.
 */
export function providerSettingsParameters(schema: Record<string, unknown> | undefined): Parameter[] {
  const properties = schema?.properties
  if (!properties || typeof properties !== 'object') return []
  const required = new Set(Array.isArray(schema?.required) ? (schema.required as unknown[]).map(String) : [])
  return Object.entries(properties as Record<string, SchemaProperty>).flatMap(([name, property]) => {
    const typeName = schemaType(property)
    if (!typeName) return []
    const parameter: Parameter = {
      name,
      type_name: typeName,
      required: required.has(name) && property.default === undefined,
      default: property.default === undefined || property.default === null ? null : String(property.default),
      description: property.description ?? '',
      choices: property.enum?.map(String) ?? null,
    }
    return [parameter]
  })
}
