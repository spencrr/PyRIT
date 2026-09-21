import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { TreeWorkspace } from '@/types'

import TreeWorkspaceDialog from './TreeWorkspaceDialog'

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const TARGET = makeTarget({
  target_registry_name: 'local-test',
  capabilities: {
    supports_multi_turn: true, supports_editable_history: true, supports_system_prompt: true,
    supported_input_modalities: ['text'], supported_output_modalities: ['text'],
    supports_json_schema: false, supports_json_output: false,
  },
})

describe('TreeWorkspaceDialog', () => {
  beforeEach(() => jest.clearAllMocks())

  it('creates a pinned workspace with a draft, not a model call', async () => {
    const user = userEvent.setup()
    const onCreate = jest.fn<Promise<void>, [TreeWorkspace]>().mockResolvedValue()
    render(<TestWrapper><TreeWorkspaceDialog targets={[TARGET]} activeTarget={TARGET} labels={{ operator: 'tester' }}
      importing={false} onClose={jest.fn()} onCreate={onCreate} /></TestWrapper>)
    await user.type(screen.getByLabelText(/first prompt/i), 'Harmless evaluation')
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    const workspace = onCreate.mock.calls[0][0]
    expect(workspace.targetIdentifierHash).toBe(TARGET.identifier.hash)
    expect(workspace.labels).toEqual({ operator: 'tester', pyrit_tree_id: workspace.id })
    expect(workspace.nodes[0]).toMatchObject({ prompt: 'Harmless evaluation', status: 'draft' })
  })

  it('blocks targets without editable history', () => {
    const target = makeTarget({ target_registry_name: 'unsupported' })
    render(<TestWrapper><TreeWorkspaceDialog targets={[target]} activeTarget={target} labels={{}}
      importing={false} onClose={jest.fn()} onCreate={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Create workspace' })).toBeDisabled()
    expect(screen.getByText(/does not advertise/i)).toBeInTheDocument()
  })

  it('imports a declarative sequence as new drafts', async () => {
    const user = userEvent.setup()
    const onCreate = jest.fn<Promise<void>, [TreeWorkspace]>().mockResolvedValue()
    render(<TestWrapper><TreeWorkspaceDialog targets={[TARGET]} activeTarget={TARGET} labels={{}}
      importing onClose={jest.fn()} onCreate={onCreate} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    const workspace = onCreate.mock.calls[0][0]
    expect(workspace.nodes).toHaveLength(2)
    expect(workspace.nodes[1].parentId).toBe(workspace.nodes[0].id)
    expect(workspace.nodes.every((node) => node.status === 'draft')).toBe(true)
  })

  it('surfaces persistence failure without closing the dialog', async () => {
    const user = userEvent.setup()
    const onClose = jest.fn()
    render(<TestWrapper><TreeWorkspaceDialog targets={[TARGET]} activeTarget={TARGET} labels={{}} importing
      onClose={onClose} onCreate={jest.fn().mockRejectedValue(new Error('Storage quota exceeded'))} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))
    expect(await screen.findByText('Storage quota exceeded')).toBeInTheDocument()
    expect(onClose).not.toHaveBeenCalled()
  })

  it('creates an objective-only workspace without inventing or running a root prompt', async () => {
    const user = userEvent.setup()
    const onCreate = jest.fn<Promise<void>, [TreeWorkspace]>().mockResolvedValue()
    render(<TestWrapper><TreeWorkspaceDialog targets={[TARGET]} activeTarget={TARGET} labels={{}}
      importing={false} onClose={jest.fn()} onCreate={onCreate} /></TestWrapper>)
    await user.selectOptions(screen.getByLabelText('Start from'), 'objective')
    await user.type(screen.getByLabelText('Evaluation objective'), 'Evaluate response grounding')
    expect(screen.queryByLabelText(/first prompt/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create workspace' }))
    await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1))
    expect(onCreate.mock.calls[0][0].nodes).toEqual([])
    expect(onCreate.mock.calls[0][0].settings?.objective).toBe('Evaluate response grounding')
  })

  it('switches creation modes while retaining entered prompt and objective text', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeWorkspaceDialog targets={[TARGET]} activeTarget={TARGET} labels={{}}
      importing={false} onClose={jest.fn()} onCreate={jest.fn()} /></TestWrapper>)
    await user.type(screen.getByLabelText(/first prompt/i), 'Keep my first prompt')
    await user.type(screen.getByLabelText('Evaluation objective'), 'Keep my objective')
    await user.selectOptions(screen.getByLabelText('Start from'), 'plan')
    expect(screen.getByLabelText('Strategy plan JSON')).toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Start from'), 'prompt')
    expect(screen.getByLabelText(/first prompt/i)).toHaveValue('Keep my first prompt')
    expect(screen.getByLabelText('Evaluation objective')).toHaveValue('Keep my objective')
  })
})
