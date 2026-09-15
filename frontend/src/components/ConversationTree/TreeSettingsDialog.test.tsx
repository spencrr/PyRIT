import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { DEFAULT_TREE_SETTINGS } from './treeModel'
import TreeSettingsDialog from './TreeSettingsDialog'

describe('TreeSettingsDialog', () => {
  it('saves budget, traversal and approval preference independently from auto-run', async () => {
    const user = userEvent.setup()
    const save = jest.fn().mockResolvedValue(true)
    render(<FluentProvider theme={webLightTheme}><TreeSettingsDialog settings={DEFAULT_TREE_SETTINGS} open onSave={save} onClose={jest.fn()} /></FluentProvider>)
    await user.selectOptions(screen.getByLabelText('Traversal'), 'depth-first')
    await user.selectOptions(screen.getByLabelText('Maximum concurrent requests'), '4')
    await user.selectOptions(screen.getByLabelText('Edge style'), 'straight')
    await user.selectOptions(screen.getByLabelText('Default node size'), 'expanded')
    await user.clear(screen.getByLabelText('Per-run operation budget'))
    await user.type(screen.getByLabelText('Per-run operation budget'), '100')
    await user.click(screen.getByRole('checkbox', { name: 'Ask before running' }))
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    await waitFor(() => expect(save).toHaveBeenCalled())
    expect(save.mock.calls[0][0]).toMatchObject({ traversal: 'depth-first', concurrency: 4, edgeStyle: 'straight', nodeSize: 'expanded', operationBudget: 100, confirmRuns: false, autoRun: false })
  })
  it('rejects invalid budgets rather than allowing unbounded work', async () => {
    const user = userEvent.setup()
    const save = jest.fn()
    render(<FluentProvider theme={webLightTheme}><TreeSettingsDialog settings={DEFAULT_TREE_SETTINGS} open onSave={save} onClose={jest.fn()} /></FluentProvider>)
    await user.clear(screen.getByLabelText('Per-run operation budget'))
    await user.type(screen.getByLabelText('Per-run operation budget'), '0')
    await user.click(screen.getByRole('button', { name: 'Save settings' }))
    expect(screen.getByText(/between 1 and 100000/)).toBeVisible()
    expect(save).not.toHaveBeenCalled()
  })
})
