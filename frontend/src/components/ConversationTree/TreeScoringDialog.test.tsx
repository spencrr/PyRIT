import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { scorersApi } from '@/services/api'
import type { ScorerCatalogEntry, ScorerInstance } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { DEFAULT_TREE_SETTINGS } from './treeModel'
import TreeScoringDialog from './TreeScoringDialog'

jest.mock('@/services/api', () => ({
  scorersApi: {
    validateScorer: jest.fn(),
    listCatalog: jest.fn(),
    listScorers: jest.fn(),
    createScorer: jest.fn(),
  },
}))

const REGISTERED_SUBSTRING: ScorerInstance = {
  scorer_id: 'substring-test',
  scorer_type: 'SubStringScorer',
  identifier_hash: 'hash',
  score_type: 'true_false',
}
const SUBSTRING_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'SubStringScorer',
  score_type: 'true_false',
  is_llm_based: false,
  parameters: [{ name: 'substring', type_name: 'str', required: true }],
}
const SELF_ASK_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'SelfAskScaleScorer',
  score_type: 'float_scale',
  is_llm_based: true,
  parameters: [
    {
      name: 'system_prompt',
      type_name: 'SeedPromptInput',
      required: true,
      input_kind: 'multiline',
      accepts_text: true,
      supports_yaml: true,
      json_schema: { type: 'object', properties: { instructions: { type: 'array' } } },
    },
    {
      name: 'scale',
      type_name: 'NumericRubric',
      required: true,
      input_kind: 'json',
      example: '{"minimum_value":0,"maximum_value":1,"category":"custom"}',
    },
  ],
}
const LIKERT_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'LikertScaleScorer',
  score_type: 'float_scale',
  is_llm_based: true,
  parameters: [{
    name: 'likert_scale',
    type_name: 'LikertScale',
    required: true,
    input_kind: 'json',
    example: '{"choices":["low","high"]}',
  }],
}
const THRESHOLD_ENTRY: ScorerCatalogEntry = {
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
      accepted_types: ['LikertScaleScorer'],
    },
  ],
}

function renderDialog(
  settings = DEFAULT_TREE_SETTINGS,
  targets = [makeTarget({ target_registry_name: 'judge-target' })],
  save = jest.fn().mockResolvedValue(true),
) {
  const view = render(
    <FluentProvider theme={webLightTheme}>
      <TreeScoringDialog settings={settings} targets={targets} open onSave={save} onClose={jest.fn()} />
    </FluentProvider>,
  )
  return { save, ...view }
}

async function openBuilder(user: ReturnType<typeof userEvent.setup>, scorerType: string): Promise<void> {
  await user.click(screen.getByText('Configure a new scorer'))
  const select = screen.getByLabelText('Scorer type')
  await within(select).findByRole('option', { name: new RegExp(`^${scorerType}`, 'i') })
  await user.selectOptions(select, scorerType)
  await screen.findByRole('region', { name: `${scorerType} configuration` })
}

describe('TreeScoringDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(scorersApi.validateScorer).mockResolvedValue({ valid: true })
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [SUBSTRING_ENTRY] })
    jest.mocked(scorersApi.listScorers).mockResolvedValue({ items: [REGISTERED_SUBSTRING] })
    jest.mocked(scorersApi.createScorer).mockResolvedValue(REGISTERED_SUBSTRING)
  })

  it('configures a local scorer, objective, and automatic scoring without scoring any response', async () => {
    const user = userEvent.setup()
    const { save } = renderDialog(DEFAULT_TREE_SETTINGS, [], jest.fn().mockResolvedValue(true))

    await user.type(screen.getByLabelText('Evaluation objective or rubric'), 'Look for the required marker')
    await openBuilder(user, 'SubStringScorer')
    const configuration = screen.getByRole('region', { name: 'SubStringScorer configuration' })
    await user.type(within(configuration).getByLabelText(/substring/i), 'marker')
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))

    await waitFor(() => expect(scorersApi.createScorer).toHaveBeenCalledWith({
      type: 'SubStringScorer',
      params: { substring: 'marker' },
    }))

    await user.click(screen.getByRole('checkbox', { name: 'Score automatically after each response' }))
    await user.click(screen.getByRole('button', { name: 'Save scoring settings' }))

    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][0]).toMatchObject({
      autoScore: true,
      objective: 'Look for the required marker',
      primaryScorerId: 'substring-test',
      scorers: [{ ...REGISTERED_SUBSTRING, scope: 'response', highIsRisk: true }],
    })
  })

  it('surfaces backend catalog failures', async () => {
    jest.mocked(scorersApi.listCatalog).mockRejectedValue(new Error('Scorer catalog unavailable'))
    renderDialog()
    expect(await screen.findByText('Scorer catalog unavailable')).toBeVisible()
  })

  it('preserves the chosen primary when a different scorer is removed', async () => {
    const user = userEvent.setup()
    const save = jest.fn().mockResolvedValue(true)
    const scorers = ['A', 'B', 'C'].map((name) => ({
      ...REGISTERED_SUBSTRING,
      scorer_id: name,
      scorer_type: name,
      scope: 'response' as const,
      highIsRisk: true,
    }))

    renderDialog({ ...DEFAULT_TREE_SETTINGS, scorers, primaryScorerId: 'B' }, [], save)

    await user.click(within(screen.getByRole('region', { name: 'C' })).getByRole('button', { name: 'Remove scorer' }))
    expect(screen.getByLabelText('Primary node metric')).toHaveValue('B')

    await user.click(screen.getByRole('button', { name: 'Save scoring settings' }))
    expect(save.mock.calls[0][0].primaryScorerId).toBe('B')
  })

  it('resets unsaved draft state after the dialog is closed and reopened', async () => {
    const user = userEvent.setup()
    const first = renderDialog({ ...DEFAULT_TREE_SETTINGS, objective: 'Original objective' }, [])
    await screen.findByRole('option', { name: /substring-test/i })

    const objective = screen.getByLabelText('Evaluation objective or rubric')
    await user.clear(objective)
    await user.type(objective, 'Unsaved draft objective')

    first.unmount()
    renderDialog({ ...DEFAULT_TREE_SETTINGS, objective: 'Reopened objective' }, [])
    await screen.findByRole('option', { name: /substring-test/i })

    expect(screen.getByLabelText('Evaluation objective or rubric')).toHaveValue('Reopened objective')
  })

  it('supports structured SeedPromptInput metadata on multiline descriptors and sends YAML wrappers', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [SELF_ASK_ENTRY] })

    renderDialog()
    await openBuilder(user, 'SelfAskScaleScorer')
    const configuration = screen.getByRole('region', { name: 'SelfAskScaleScorer configuration' })
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))
    expect(screen.getByText('system_prompt is required.')).toBeVisible()
    expect(scorersApi.createScorer).not.toHaveBeenCalled()

    expect(within(configuration).getByRole('combobox', { name: /system_prompt format/i })).toHaveValue('text')
    await user.selectOptions(within(configuration).getByRole('combobox', { name: /system_prompt format/i }), 'yaml')
    await user.type(within(configuration).getByRole('textbox', { name: /system_prompt/i }), 'Return a numeric score_value and rationale.')
    await user.selectOptions(await screen.findByLabelText(/Judge target/i), 'judge-target')
    await user.click(within(configuration).getByRole('button', { name: 'Use example scale' }))
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))

    await waitFor(() => expect(scorersApi.createScorer).toHaveBeenCalledWith({
      type: 'SelfAskScaleScorer',
      params: {
        system_prompt: { yaml: 'Return a numeric score_value and rationale.' },
        scale: { minimum_value: 0, maximum_value: 1, category: 'custom' },
        chat_target: 'judge-target',
      },
    }))
  })

  it('does not offer a broken create action when a required Python-only parameter is unsupported', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [{
      scorer_type: 'CustomScorer',
      score_type: 'unknown',
      is_llm_based: false,
      parameters: [{ name: 'handler', type_name: 'ResponseHandler', required: true, input_kind: 'unsupported' }],
    }] })

    renderDialog(DEFAULT_TREE_SETTINGS, [])
    await openBuilder(user, 'CustomScorer')
    expect(screen.getByRole('button', { name: 'Create and select scorer' })).toBeDisabled()
    expect(screen.getByText(/requires Python configuration/i)).toBeVisible()
  })

  it('runs validate configuration without creating a scorer instance', async () => {
    const user = userEvent.setup()

    renderDialog(DEFAULT_TREE_SETTINGS, [])
    await openBuilder(user, 'SubStringScorer')
    const configuration = screen.getByRole('region', { name: 'SubStringScorer configuration' })
    await user.type(within(configuration).getByLabelText(/substring/i), 'marker')
    await user.click(screen.getByRole('button', { name: 'Validate configuration' }))

    await waitFor(() => expect(scorersApi.validateScorer).toHaveBeenCalledWith({
      type: 'SubStringScorer',
      params: { substring: 'marker' },
    }))
    expect(scorersApi.createScorer).not.toHaveBeenCalled()
    expect(await screen.findByText('Configuration is valid.')).toBeVisible()
  })

  it('creates only the root scorer and sends nested inline configuration atomically', async () => {
    const user = userEvent.setup()
    const nestedInstance: ScorerInstance = {
      scorer_id: 'threshold-scorer',
      scorer_type: 'ThresholdGateScorer',
      identifier_hash: 'threshold-hash',
      score_type: 'float_scale',
    }
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [THRESHOLD_ENTRY, LIKERT_ENTRY] })
    jest.mocked(scorersApi.createScorer).mockResolvedValue(nestedInstance)

    renderDialog(DEFAULT_TREE_SETTINGS, [makeTarget({ target_registry_name: 'judge-target' })])
    await openBuilder(user, 'ThresholdGateScorer')
    const configuration = screen.getByRole('region', { name: 'ThresholdGateScorer configuration' })
    expect(within(configuration).queryByLabelText(/Judge target/i)).not.toBeInTheDocument()

    await user.type(within(configuration).getByLabelText(/threshold/i), '0.8')
    await user.selectOptions(within(configuration).getByLabelText(/nested source/i), 'inline')
    await user.selectOptions(within(configuration).getByLabelText(/nested scorer type/i), 'LikertScaleScorer')
    await user.click(within(configuration).getByRole('button', { name: 'Use example likert_scale' }))
    await user.selectOptions(await screen.findByLabelText(/Judge target/i), 'judge-target')
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))

    await waitFor(() => expect(scorersApi.createScorer).toHaveBeenCalledWith({
      type: 'ThresholdGateScorer',
      params: {
        threshold: 0.8,
        nested: {
          type: 'LikertScaleScorer',
          params: {
            likert_scale: { choices: ['low', 'high'] },
            chat_target: 'judge-target',
          },
        },
      },
    }))
    expect(scorersApi.createScorer).toHaveBeenCalledTimes(1)
  })
})
