import type { ScorerCatalogEntry, ScorerParameter } from '@/types'

import {
  buildScorerParameters,
  createInlineReferenceValue,
  createRegisteredReferenceValue,
  getScorerBuilderParameters,
  type StructuredScorerValue,
} from './scorerForm'

const SCALE: ScorerParameter = { name: 'scale', type_name: 'NumericRubric', required: true, input_kind: 'json' }
const SUBSTRING: ScorerCatalogEntry = {
  scorer_type: 'SubStringScorer',
  score_type: 'true_false',
  is_llm_based: false,
  parameters: [{ name: 'substring', type_name: 'str', required: true }],
}
const LIKERT: ScorerCatalogEntry = {
  scorer_type: 'LikertScaleScorer',
  score_type: 'float_scale',
  is_llm_based: true,
  parameters: [{
    name: 'likert_scale',
    type_name: 'LikertScale',
    required: true,
    input_kind: 'json',
    json_schema: { type: 'object', properties: { choices: { type: 'array' } } },
  }],
}
const THRESHOLD: ScorerCatalogEntry = {
  scorer_type: 'ThresholdGateScorer',
  score_type: 'float_scale',
  is_llm_based: false,
  parameters: [
    { name: 'threshold', type_name: 'float', required: true },
    {
      name: 'nested',
      type_name: 'Scorer',
      required: true,
      reference_kind: 'scorer',
      accepts_inline: true,
      accepted_types: ['LikertScaleScorer', 'SubStringScorer'],
    },
    {
      name: 'fallbacks',
      type_name: 'list[Scorer]',
      is_list: true,
      required: false,
      reference_kind: 'scorer',
      accepts_inline: true,
      accepted_types: ['SubStringScorer'],
    },
  ],
}
const PYTHON_ONLY: ScorerCatalogEntry = {
  scorer_type: 'PythonOnlyScorer',
  score_type: 'unknown',
  is_llm_based: false,
  parameters: [{ name: 'handler', type_name: 'ResponseHandler', required: true, input_kind: 'unsupported' }],
}
const VIDEO_TRUE_FALSE: ScorerCatalogEntry = {
  scorer_type: 'VideoTrueFalseScorer',
  score_type: 'true_false',
  is_llm_based: false,
  parameters: [
    {
      name: 'image_scorer',
      type_name: 'Scorer',
      required: true,
      reference_kind: 'scorer',
    },
    {
      name: 'audio_scorer',
      type_name: 'Scorer',
      required: false,
      reference_kind: 'scorer',
    },
  ],
}

describe('buildScorerParameters', () => {
  it.each(['{invalid', '"not an object"', 'null'])('rejects invalid structured input %s', (scale: string) => {
    expect(buildScorerParameters([SCALE], { scale }).ok).toBe(false)
  })

  it('requires a rubric and parses valid JSON without evaluating code', () => {
    expect(buildScorerParameters([SCALE], {}).ok).toBe(false)
    expect(buildScorerParameters([SCALE], { scale: '{"minimum_value":0,"maximum_value":10,"category":"math"}' }))
      .toEqual({ ok: true, parameters: { scale: { minimum_value: 0, maximum_value: 10, category: 'math' } } })
  })

  it('accepts SeedPrompt text, JSON, and YAML wrappers from metadata', () => {
    const seedPrompt: ScorerParameter = {
      name: 'seed_prompt',
      type_name: 'SeedPrompt',
      required: true,
      input_kind: 'multiline',
      accepts_text: true,
      supports_yaml: true,
      json_schema: { type: 'object', properties: { instructions: { type: 'array' } } },
    }
    const textValue: StructuredScorerValue = { format: 'text', value: 'Write a single declarative prompt.' }
    const jsonValue: StructuredScorerValue = { format: 'json', value: '{"instructions":["stay specific"]}' }
    const yamlValue: StructuredScorerValue = { format: 'yaml', value: 'instructions:\n  - stay specific' }

    expect(buildScorerParameters([seedPrompt], { seed_prompt: textValue })).toEqual({
      ok: true,
      parameters: { seed_prompt: 'Write a single declarative prompt.' },
    })
    expect(buildScorerParameters([seedPrompt], { seed_prompt: jsonValue })).toEqual({
      ok: true,
      parameters: { seed_prompt: { instructions: ['stay specific'] } },
    })
    expect(buildScorerParameters([seedPrompt], { seed_prompt: yamlValue })).toEqual({
      ok: true,
      parameters: { seed_prompt: { yaml: 'instructions:\n  - stay specific' } },
    })
  })

  it('accepts object and array structured schemas for dict and set adapters', () => {
    const dictParameter: ScorerParameter = {
      name: 'metadata',
      type_name: 'dict[str,str]',
      required: true,
      input_kind: 'json',
      json_schema: { type: 'object', additionalProperties: { type: 'string' } },
    }
    const setParameter: ScorerParameter = {
      name: 'categories',
      type_name: 'set[str]',
      required: true,
      input_kind: 'json',
      json_schema: { type: 'array', items: { type: 'string' } },
    }

    expect(buildScorerParameters([dictParameter, setParameter], {
      metadata: { format: 'json', value: '{"owner":"security","stage":"prod"}' },
      categories: { format: 'json', value: '["toxicity","fraud"]' },
    })).toEqual({
      ok: true,
      parameters: {
        metadata: { owner: 'security', stage: 'prod' },
        categories: ['toxicity', 'fraud'],
      },
    })
  })

  it('builds nested scorer payloads for single and list references', () => {
    const parameters = getScorerBuilderParameters(THRESHOLD)

    expect(buildScorerParameters(parameters, {
      threshold: '0.75',
      nested: createInlineReferenceValue('LikertScaleScorer', {
        likert_scale: { format: 'json', value: '{"choices":["low","medium","high"]}' },
        chat_target: createRegisteredReferenceValue('judge-target'),
      }),
      fallbacks: [
        createRegisteredReferenceValue('substring-prod'),
        createInlineReferenceValue('SubStringScorer', { substring: 'needle' }),
      ],
    }, { catalog: [THRESHOLD, LIKERT, SUBSTRING] })).toEqual({
      ok: true,
      parameters: {
        threshold: 0.75,
        nested: {
          type: 'LikertScaleScorer',
          params: {
            likert_scale: { choices: ['low', 'medium', 'high'] },
            chat_target: 'judge-target',
          },
        },
        fallbacks: [
          'substring-prod',
          { type: 'SubStringScorer', params: { substring: 'needle' } },
        ],
      },
    })
  })

  it('blocks nested scorers that still require Python-only configuration', () => {
    const parameters = getScorerBuilderParameters(THRESHOLD)

    expect(buildScorerParameters(parameters, {
      threshold: '0.4',
      nested: createInlineReferenceValue('PythonOnlyScorer', {}),
    }, { catalog: [THRESHOLD, PYTHON_ONLY] })).toEqual({
      ok: false,
      error: 'handler requires Python configuration. Register this scorer in an initializer instead.',
    })
  })

  it('preserves category arrays exposed from sequence-or-string constructor parameters', () => {
    const categories: ScorerParameter = {
      name: 'harm_categories',
      type_name: 'list[str]',
      is_list: true,
      required: true,
      input_kind: 'field',
    }

    expect(buildScorerParameters([categories], { harm_categories: 'category one, category two' })).toEqual({
      ok: true,
      parameters: { harm_categories: ['category one', 'category two'] },
    })
  })

  it('omits unset optional singular registered scorer references', () => {
    const parameters = getScorerBuilderParameters(VIDEO_TRUE_FALSE)

    expect(buildScorerParameters(parameters, {
      image_scorer: createRegisteredReferenceValue('image-scorer'),
      audio_scorer: createRegisteredReferenceValue(''),
    }, { catalog: [VIDEO_TRUE_FALSE] })).toEqual({
      ok: true,
      parameters: { image_scorer: 'image-scorer' },
    })
  })

  it('keeps errors for incomplete optional inline scorer references', () => {
    const parameters = getScorerBuilderParameters(VIDEO_TRUE_FALSE)

    expect(buildScorerParameters(parameters, {
      image_scorer: createRegisteredReferenceValue('image-scorer'),
      audio_scorer: createInlineReferenceValue('', {}),
    }, { catalog: [VIDEO_TRUE_FALSE] })).toEqual({
      ok: false,
      error: 'audio_scorer scorer type is required.',
    })
  })

  it('keeps errors for empty optional scorer list entries', () => {
    const optionalList: ScorerParameter = {
      name: 'audio_scorers',
      type_name: 'list[Scorer]',
      is_list: true,
      required: false,
      reference_kind: 'scorer',
    }

    expect(buildScorerParameters([optionalList], {
      audio_scorers: [createRegisteredReferenceValue('')],
    }, { catalog: [] })).toEqual({
      ok: false,
      error: 'audio_scorers is required.',
    })
  })
})
