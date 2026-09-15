import { useState } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { ScorerCatalogEntry, ScorerInstance } from '@/types'

import ScorerBuilder from './ScorerBuilder'
import { getInitialScorerFormValues, type ScorerFormValue } from './scorerForm'

const SUBSTRING_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'SubStringScorer',
  score_type: 'true_false',
  is_llm_based: false,
  parameters: [{ name: 'substring', type_name: 'str', required: true }],
}
const AGGREGATOR_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'SafeAggregatorScorer',
  score_type: 'float_scale',
  is_llm_based: false,
  parameters: [{
    name: 'scorers',
    type_name: 'list[Scorer]',
    is_list: true,
    required: true,
    reference_kind: 'scorer',
    accepts_inline: true,
    accepted_types: ['SubStringScorer'],
  }],
}
const MANY_COMPATIBLE_TYPES = Array.from({ length: 38 }, (_, index: number) => `CompatibleScorer${index + 1}`)
const LONG_HINT_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'InverterScorer',
  score_type: 'float_scale',
  is_llm_based: false,
  parameters: [{
    name: 'delegate',
    type_name: 'Scorer',
    required: true,
    reference_kind: 'scorer',
    accepted_types: MANY_COMPATIBLE_TYPES,
  }],
}
const SHORT_HINT_ENTRY: ScorerCatalogEntry = {
  scorer_type: 'CompactHintScorer',
  score_type: 'float_scale',
  is_llm_based: false,
  parameters: [{
    name: 'delegate',
    type_name: 'Scorer',
    required: true,
    reference_kind: 'scorer',
    accepted_types: ['AlphaScorer', 'BetaScorer', 'GammaScorer'],
  }],
}
const REGISTERED: ScorerInstance[] = [{
  scorer_id: 'registered-a',
  scorer_type: 'SubStringScorer',
  identifier_hash: 'registered-hash',
  score_type: 'true_false',
}, {
  scorer_id: 'registered-b',
  scorer_type: 'SubStringScorer',
  identifier_hash: 'registered-hash-b',
  score_type: 'true_false',
}]

interface HarnessProps {
  entry?: ScorerCatalogEntry
  catalog?: ScorerCatalogEntry[]
}

function Harness({ entry = AGGREGATOR_ENTRY, catalog = [AGGREGATOR_ENTRY, SUBSTRING_ENTRY] }: HarnessProps) {
  const [values, setValues] = useState<Record<string, ScorerFormValue>>(
    getInitialScorerFormValues(entry, undefined, catalog, { prefillDefaults: false }),
  )

  return (
    <FluentProvider theme={webLightTheme}>
      <ScorerBuilder
        entry={entry}
        catalog={catalog}
        instances={REGISTERED}
        targets={[]}
        values={values}
        disabled={false}
        onChange={setValues}
      />
    </FluentProvider>
  )
}

describe('ScorerBuilder', () => {
  it('summarizes long compatible type lists instead of rendering them verbatim', () => {
    render(<Harness entry={LONG_HINT_ENTRY} catalog={[LONG_HINT_ENTRY, SUBSTRING_ENTRY]} />)

    expect(screen.getByText('38 compatible scorer types (filtered below).')).toBeInTheDocument()
    expect(screen.queryByText(/CompatibleScorer1, CompatibleScorer2/)).not.toBeInTheDocument()
  })

  it('shows short compatible type lists verbatim', () => {
    render(<Harness entry={SHORT_HINT_ENTRY} catalog={[SHORT_HINT_ENTRY, SUBSTRING_ENTRY]} />)

    expect(screen.getByText('Compatible scorer types: AlphaScorer, BetaScorer, GammaScorer.')).toBeInTheDocument()
  })

  it('adds and removes nested inline scorer references', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: 'Add scorer' }))
    await user.click(screen.getByRole('button', { name: 'Add scorer' }))

    let items = screen.getAllByRole('region', { name: /scorers \d/i })
    await user.selectOptions(within(items[0]).getAllByRole('combobox')[1], 'registered-a')
    await user.selectOptions(within(items[1]).getAllByRole('combobox')[0], 'inline')
    items = screen.getAllByRole('region', { name: /scorers \d/i })
    await user.selectOptions(within(items[1]).getAllByRole('combobox')[1], 'SubStringScorer')
    items = screen.getAllByRole('region', { name: /scorers \d/i })
    await user.type(within(items[1]).getByLabelText(/substring/i), 'marker')

    await user.click(within(items[0]).getByRole('button', { name: 'Remove' }))

    items = screen.getAllByRole('region', { name: /scorers \d/i })
    expect(items).toHaveLength(1)
    expect(within(items[0]).getByLabelText(/substring/i)).toHaveValue('marker')
  })

  it('reorders registered scorer references', async () => {
    const user = userEvent.setup()
    render(<Harness />)

    await user.click(screen.getByRole('button', { name: 'Add scorer' }))
    await user.click(screen.getByRole('button', { name: 'Add scorer' }))

    let items = screen.getAllByRole('region', { name: /scorers \d/i })
    await user.selectOptions(within(items[0]).getAllByRole('combobox')[1], 'registered-a')
    await user.selectOptions(within(items[1]).getAllByRole('combobox')[1], 'registered-b')
    await user.click(within(items[1]).getByRole('button', { name: 'Move up' }))

    items = screen.getAllByRole('region', { name: /scorers \d/i })
    expect(within(items[0]).getAllByRole('combobox')[1]).toHaveValue('registered-b')
    expect(within(items[1]).getAllByRole('combobox')[1]).toHaveValue('registered-a')
  })
})
