import { useState } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { ConverterCatalogEntry, TreeConverterSpec } from '@/types'

import TreePipelineEditor from './TreePipelineEditor'

const CATALOG: ConverterCatalogEntry[] = [
  { converter_type: 'First', supported_input_types: ['text'], supported_output_types: ['text'], is_llm_based: false,
    parameters: [
      { name: 'mode', type_name: 'str', required: true, choices: ['one', 'two'] },
      { name: 'enabled', type_name: 'bool', required: false },
    ] },
  { converter_type: 'Second', supported_input_types: ['text'], supported_output_types: ['text'], is_llm_based: false, parameters: [] },
]

function Harness({ initial, onChange }: { initial: TreeConverterSpec[]; onChange: (pipeline: TreeConverterSpec[]) => void }) {
  const [pipeline, setPipeline] = useState(initial)
  return <FluentProvider theme={webLightTheme}>
    <TreePipelineEditor converters={pipeline} catalog={CATALOG} targets={[]} disabled={false}
      onChange={(next: TreeConverterSpec[]) => { setPipeline(next); onChange(next) }} />
  </FluentProvider>
}

describe('TreePipelineEditor', () => {
  it('edits declared choices and booleans while retaining unexposed imported params', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness initial={[{ type: 'First', params: { mode: 'one', enabled: true, custom_param: 'retained' } }]} onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Edit converter 1' }))
    await user.selectOptions(screen.getByLabelText(/mode/), 'two')
    await user.selectOptions(screen.getByLabelText('enabled'), 'false')
    await user.click(screen.getByRole('button', { name: 'Apply converter changes' }))
    expect(onChange).toHaveBeenLastCalledWith([{ type: 'First', params: { mode: 'two', enabled: false, custom_param: 'retained' } }])
  })

  it('preserves step parameters when reordering, and supports removal', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    const first = { type: 'First', params: { mode: 'two' } }
    const second = { type: 'Second', params: {} }
    render(<Harness initial={[first, second]} onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Move converter 2 up' }))
    expect(onChange).toHaveBeenLastCalledWith([second, first])
    await user.click(screen.getByRole('button', { name: 'Move converter 1 down' }))
    expect(onChange).toHaveBeenLastCalledWith([first, second])
    await user.click(screen.getByRole('button', { name: 'Remove converter 2' }))
    expect(onChange).toHaveBeenLastCalledWith([first])
  })

  it('validates required parameters and can cancel unapplied edits', async () => {
    const user = userEvent.setup()
    const onChange = jest.fn()
    render(<Harness initial={[]} onChange={onChange} />)
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(screen.getByLabelText('Converter'), 'First')
    await user.click(screen.getByRole('button', { name: 'Add converter', exact: true }))
    expect(screen.getByText('mode is required.')).toBeVisible()
    expect(onChange).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Cancel converter' }))
    expect(screen.queryByLabelText('Converter')).not.toBeInTheDocument()
  })
})
