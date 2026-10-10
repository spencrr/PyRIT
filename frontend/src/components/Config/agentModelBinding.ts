import type { HarnessProfile, InferenceRequirements, InferenceWireApi, TargetInstance } from '@/types'
import { targetModelName, targetUnderlyingModelName } from '@/utils/targetIdentity'

/** Compatibility of model transport and harness configuration, not proof of model tool behavior. */
export function modelBindingOptions(
  target: TargetInstance,
  harness: HarnessProfile['harness_kind'],
  requirements?: InferenceRequirements,
): { protocols: InferenceWireApi[]; reason: string | null } {
  const capabilities = target.inference_capabilities
  if (!capabilities) return { protocols: [], reason: 'No inference connection' }
  if (capabilities.blocked_reason) return { protocols: [], reason: capabilities.blocked_reason }
  if (harness === 'acp') return { protocols: [], reason: 'Custom harness needs a model adapter' }
  if ((requirements?.streaming !== false && !capabilities.streaming)
    || (requirements?.tool_calls !== false && !capabilities.tool_calls)) {
    return { protocols: [], reason: 'Streaming and tool passthrough required' }
  }
  if ((requirements?.input_modalities ?? ['text']).some((modality) => !capabilities.input_modalities.includes(modality))) {
    return { protocols: [], reason: 'Missing required input modality' }
  }
  const protocols = capabilities.wire_apis.filter((api) => harness !== 'opencode' || api === 'completions')
  return { protocols, reason: protocols.length ? null : 'No compatible protocol' }
}

export function modelBindingLabel(target: TargetInstance): string {
  const model = targetUnderlyingModelName(target) || targetModelName(target)
  return model ? `${model} · ${target.target_registry_name}` : target.target_registry_name
}
