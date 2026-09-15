import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { scorersApi } from '@/services/api'
import type { ScorerInstance } from '@/types'
import { makeTarget } from '@/test-utils/targetFixtures'

import { DEFAULT_TREE_SETTINGS } from './treeModel'
import TreeScoringDialog from './TreeScoringDialog'

jest.mock('@/services/api', () => ({
  scorersApi: { listCatalog: jest.fn(), listScorers: jest.fn(), createScorer: jest.fn() },
}))
const SCORER: ScorerInstance = { scorer_id: 'substring-test', scorer_type: 'SubStringScorer', identifier_hash: 'hash', score_type: 'true_false' }
describe('TreeScoringDialog', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [{
      scorer_type: 'SubStringScorer', score_type: 'true_false', is_llm_based: false,
      parameters: [{ name: 'substring', type_name: 'str', required: true }],
    }] })
    jest.mocked(scorersApi.listScorers).mockResolvedValue({ items: [SCORER] })
    jest.mocked(scorersApi.createScorer).mockResolvedValue(SCORER)
  })
  it('configures a local scorer, objective and automatic scoring without scoring any response', async () => {
    const user = userEvent.setup()
    const save = jest.fn().mockResolvedValue(true)
    render(<FluentProvider theme={webLightTheme}><TreeScoringDialog settings={DEFAULT_TREE_SETTINGS} targets={[]} open onSave={save} onClose={jest.fn()} /></FluentProvider>)
    await user.type(screen.getByLabelText('Evaluation objective or rubric'), 'Look for the required marker')
    await user.click(screen.getByText('Configure a new scorer'))
    await user.selectOptions(screen.getByLabelText('Scorer type'), 'SubStringScorer')
    await user.type(screen.getByLabelText(/substring/), 'marker')
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))
    await waitFor(() => expect(scorersApi.createScorer).toHaveBeenCalledWith({ type: 'SubStringScorer', params: { substring: 'marker' } }))
    await user.click(screen.getByRole('checkbox', { name: 'Score automatically after each response' }))
    await user.click(screen.getByRole('button', { name: 'Save scoring settings' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][0]).toMatchObject({
      autoScore: true, objective: 'Look for the required marker', primaryScorerId: 'substring-test',
      scorers: [{ ...SCORER, scope: 'response', highIsRisk: true }],
    })
  })
  it('surfaces backend catalog failures', async () => {
    jest.mocked(scorersApi.listCatalog).mockRejectedValue(new Error('Scorer catalog unavailable'))
    render(<FluentProvider theme={webLightTheme}><TreeScoringDialog settings={DEFAULT_TREE_SETTINGS} targets={[]} open onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    expect(await screen.findByText('Scorer catalog unavailable')).toBeVisible()
  })

  it('preserves the chosen primary when a different scorer is removed', async () => {
    const user = userEvent.setup()
    const save = jest.fn().mockResolvedValue(true)
    const scorers = ['A', 'B', 'C'].map((name) => ({
      ...SCORER, scorer_id: name, scorer_type: name, scope: 'response' as const, highIsRisk: true,
    }))
    render(<FluentProvider theme={webLightTheme}><TreeScoringDialog
      settings={{ ...DEFAULT_TREE_SETTINGS, scorers, primaryScorerId: 'B' }} targets={[]} open onSave={save} onClose={jest.fn()} /></FluentProvider>)
    await user.click(within(screen.getByRole('region', { name: 'C' })).getByRole('button', { name: 'Remove scorer' }))
    expect(screen.getByLabelText('Primary node metric')).toHaveValue('B')
    await user.click(screen.getByRole('button', { name: 'Save scoring settings' }))
    expect(save.mock.calls[0][0].primaryScorerId).toBe('B')
  })

  it('shows required scale scorer inputs and sends the rubric as a JSON object', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [{
      scorer_type: 'SelfAskScaleScorer', score_type: 'float_scale', is_llm_based: true,
      parameters: [
        { name: 'system_prompt', type_name: 'str', required: true, input_kind: 'multiline' },
        { name: 'scale', type_name: 'NumericRubric', required: true, input_kind: 'json',
          example: '{"minimum_value":0,"maximum_value":1,"category":"custom"}' },
      ],
    }] })
    render(<FluentProvider theme={webLightTheme}><TreeScoringDialog settings={DEFAULT_TREE_SETTINGS}
      targets={[makeTarget({ target_registry_name: 'judge' })]} open onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await user.click(screen.getByText('Configure a new scorer'))
    await user.selectOptions(screen.getByLabelText('Scorer type'), 'SelfAskScaleScorer')
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))
    expect(screen.getByText('system_prompt is required.')).toBeVisible()
    expect(scorersApi.createScorer).not.toHaveBeenCalled()
    await user.type(screen.getByRole('textbox', { name: /system_prompt/ }), 'Return a numeric score_value and rationale.')
    await user.selectOptions(screen.getByLabelText(/Judge target/), 'judge')
    await user.click(screen.getByRole('button', { name: 'Use example scale' }))
    await user.click(screen.getByRole('button', { name: 'Create and select scorer' }))
    await waitFor(() => expect(scorersApi.createScorer).toHaveBeenCalledWith({
      type: 'SelfAskScaleScorer',
      params: {
        chat_target: 'judge',
        system_prompt: 'Return a numeric score_value and rationale.',
        scale: { minimum_value: 0, maximum_value: 1, category: 'custom' },
      },
    }))
  })

  it('does not offer a broken create action when a required Python-only parameter is unsupported', async () => {
    const user = userEvent.setup()
    jest.mocked(scorersApi.listCatalog).mockResolvedValue({ items: [{
      scorer_type: 'CustomScorer', score_type: 'unknown', is_llm_based: false,
      parameters: [{ name: 'handler', type_name: 'ResponseHandler', required: true, input_kind: 'unsupported' }],
    }] })
    render(<FluentProvider theme={webLightTheme}><TreeScoringDialog settings={DEFAULT_TREE_SETTINGS}
      targets={[]} open onSave={jest.fn()} onClose={jest.fn()} /></FluentProvider>)
    await user.click(screen.getByText('Configure a new scorer'))
    await user.selectOptions(screen.getByLabelText('Scorer type'), 'CustomScorer')
    expect(screen.getByRole('button', { name: 'Create and select scorer' })).toBeDisabled()
    expect(screen.getByText(/requires Python-only configuration for handler/)).toBeVisible()
  })
})
