import type { ScorerParameter } from '@/types'

import { buildScorerParameters } from './scorerForm'

const SCALE: ScorerParameter = { name: 'scale', type_name: 'NumericRubric', required: true, input_kind: 'json' }

describe('buildScorerParameters', () => {
  it.each(['{invalid', '"not an object"', 'null', '[]'])('rejects invalid structured input %s', (scale: string) => {
    expect(buildScorerParameters([SCALE], { scale }).ok).toBe(false)
  })
  it('requires a rubric and parses valid JSON without evaluating code', () => {
    expect(buildScorerParameters([SCALE], {}).ok).toBe(false)
    expect(buildScorerParameters([SCALE], { scale: '{"minimum_value":0,"maximum_value":10,"category":"math"}' }))
      .toEqual({ ok: true, parameters: { scale: { minimum_value: 0, maximum_value: 10, category: 'math' } } })
  })
  it('does not submit unsupported optional objects', () => {
    expect(buildScorerParameters([{ name: 'validator', type_name: 'Validator', required: false, input_kind: 'unsupported' }], {}))
      .toEqual({ ok: true, parameters: {} })
  })
  it('preserves category arrays exposed from sequence-or-string constructor parameters', () => {
    const categories: ScorerParameter = {
      name: 'harm_categories', type_name: 'list[str]', is_list: true, required: true, input_kind: 'field',
    }
    expect(buildScorerParameters([categories], { harm_categories: 'category one, category two' })).toEqual({
      ok: true, parameters: { harm_categories: ['category one', 'category two'] },
    })
  })
})
