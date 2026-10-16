import { providerSettingsParameters } from './environmentProviders'

describe('providerSettingsParameters', () => {
  it('projects flat scalar schema properties and skips nested or untyped ones', () => {
    const parameters = providerSettingsParameters({
      type: 'object',
      required: ['pool', 'region'],
      properties: {
        pool: { type: 'string', description: 'Pool name' },
        region: { type: ['string', 'null'], default: 'eastus2', enum: ['eastus2', 'westus3'] },
        cpu_count: { type: 'integer', default: 2 },
        memory_gib: { type: 'number' },
        egress: { type: 'boolean', default: false },
        nested: { type: 'object', properties: {} },
        untyped: { description: 'no type' },
      },
    })
    expect(parameters).toEqual([
      { name: 'pool', type_name: 'str', required: true, default: null, description: 'Pool name', choices: null },
      { name: 'region', type_name: 'str', required: false, default: 'eastus2', description: '', choices: ['eastus2', 'westus3'] },
      { name: 'cpu_count', type_name: 'int', required: false, default: '2', description: '', choices: null },
      { name: 'memory_gib', type_name: 'float', required: false, default: null, description: '', choices: null },
      { name: 'egress', type_name: 'bool', required: false, default: 'false', description: '', choices: null },
    ])
  })

  it('returns nothing for providers without a settings schema', () => {
    expect(providerSettingsParameters(undefined)).toEqual([])
    expect(providerSettingsParameters({})).toEqual([])
    expect(providerSettingsParameters({ properties: 'invalid' })).toEqual([])
  })
})
