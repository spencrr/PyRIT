import type { HarnessAdapterInfo } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { modelBindingOptions } from './agentModelBinding'

describe('modelBindingOptions', () => {
  const adapter: HarnessAdapterInfo = {
    name: 'team_v1', display_name: 'Team agent', default_command: ['team-agent'], wire_apis: ['responses'],
  }

  it('uses installed adapter metadata rather than a closed harness switch', () => {
    const target = makeTarget({ target_registry_name: 'source' })
    target.inference_capabilities = {
      wire_apis: ['responses', 'completions'], streaming: true, tool_calls: true,
      input_modalities: ['text'], blocked_reason: null,
    }
    expect(modelBindingOptions(target, 'team_v1', undefined, [adapter])).toEqual({ protocols: ['responses'], reason: null })
    expect(modelBindingOptions(target, 'team_v1', undefined, []).reason).toBe('Harness adapter is unavailable')
    expect(modelBindingOptions(target, 'team_v1', undefined, [{ ...adapter, wire_apis: [] }]).protocols).toEqual([])
    expect(modelBindingOptions(target, 'copilot', undefined, []).protocols).toEqual([])
  })
})
