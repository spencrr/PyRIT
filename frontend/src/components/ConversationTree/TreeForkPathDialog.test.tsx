import { useState } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { ConverterCatalogEntry, TreeCommand, TreeNode, TreeWorkspace } from '@/types'

import { applyTreeCommand, createTreeWorkspace, getTreeSettings, MAX_TREE_NODES, parseTreeWorkspace } from './treeModel'
import TreeForkPathDialog from './TreeForkPathDialog'

const CATALOG: ConverterCatalogEntry[] = [
  {
    converter_type: 'First', supported_input_types: ['text'], supported_output_types: ['text'], is_llm_based: false,
    parameters: [
      { name: 'mode', type_name: 'str', required: true, choices: ['one', 'two'] },
      { name: 'enabled', type_name: 'bool', required: false },
    ],
  },
  {
    converter_type: 'Rewrite', supported_input_types: ['text'], supported_output_types: ['text'],
    is_llm_based: true, parameters: [],
  },
]
const TARGETS = [makeTarget({
  target_registry_name: 'rewrite-target',
  capabilities: {
    supports_multi_turn: true, supports_editable_history: true, supports_json_schema: false,
    supports_json_output: false, supports_system_prompt: true,
    supported_input_modalities: ['text'], supported_output_modalities: ['text'],
  },
})]

function TestWrapper({ children }: { children: React.ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function node(id: string, parentId: string | null = null): TreeNode {
  return { id, parentId, prompt: `${id} prompt`, converters: [], status: 'draft', pruned: false, kept: false }
}

function fixture(): TreeWorkspace {
  const workspace = createTreeWorkspace({
    name: 'Path forks', targetRegistryName: 'target', targetIdentifierHash: 'hash', systemPrompt: '', labels: {},
  })
  return parseTreeWorkspace(JSON.stringify({
    ...workspace,
    settings: { ...getTreeSettings(workspace), autoRun: true },
    nodes: [
      node('root'),
      { ...node('start', 'root'), converters: [{ type: 'First', params: { mode: 'one', enabled: true, retained: { custom: 1 } } }] },
      node('end', 'start'),
      node('side', 'start'),
      node('side-leaf', 'side'),
      node('beyond', 'end'),
      node('root-side', 'root'),
      node('unrelated'),
    ],
  }))
}

describe('TreeForkPathDialog', () => {
  const defaultProps = {
    selectedId: 'end', open: true, catalog: CATALOG, targets: TARGETS,
    onClose: jest.fn(), onFork: jest.fn(), onPreview: jest.fn(),
  }

  beforeEach(() => { jest.clearAllMocks() })

  it('should preview the exact ordered path, edit its first prompt and full pipeline, and submit once', async () => {
    const user = userEvent.setup()
    const workspace = fixture()
    const original = JSON.stringify(workspace)
    const onFork = jest.fn().mockResolvedValue(true)
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} onFork={onFork} /></TestWrapper>)
    expect(screen.getByLabelText('Path endpoint')).toHaveValue('end')
    expect(screen.getByLabelText('Starting ancestor')).toHaveValue('end')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['end'])
    await user.selectOptions(screen.getByLabelText('Starting ancestor'), 'start')
    expect(screen.getByLabelText('First prompt')).toHaveValue('start prompt')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['start', 'end'])
    expect(screen.getByText('2 prompts will be copied as drafts.')).toBeVisible()
    expect(screen.getByText(/excluded \(3 nodes\)/)).toBeVisible()
    const preview = screen.getByRole('list', { name: 'Prompts to copy' })
    expect(within(preview).getAllByRole('listitem')).toHaveLength(2)
    expect(within(preview).getByText('start prompt')).toBeVisible()
    expect(within(preview).getByText('end prompt')).toBeVisible()
    expect(within(preview).queryByText('side prompt')).not.toBeInTheDocument()
    await user.clear(screen.getByLabelText('First prompt'))
    await user.type(screen.getByLabelText('First prompt'), 'Changed first prompt')
    expect(within(preview).getByText('Changed first prompt')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Edit converter 1' }))
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText(/mode/), 'two')
    await user.selectOptions(screen.getByLabelText('enabled'), 'false')
    await user.click(screen.getByRole('button', { name: 'Apply converter changes' }))
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(screen.getByLabelText('Converter'), 'Rewrite')
    await user.selectOptions(screen.getByLabelText('Rewrite target'), 'rewrite-target')
    await user.click(screen.getByRole('button', { name: 'Add converter', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Move converter 2 up' }))
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(screen.getByLabelText('Converter'), 'Rewrite')
    await user.click(screen.getByRole('button', { name: 'Add converter', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Remove converter 3' }))
    await user.click(screen.getByRole('button', { name: 'Fork path' }))
    expect(onFork).toHaveBeenCalledTimes(1)
    expect(onFork).toHaveBeenCalledWith({
      type: 'forkPath', nodeId: 'start', descendantId: 'end', prompt: 'Changed first prompt',
      converters: [
        { type: 'Rewrite', params: { converter_target: 'rewrite-target' } },
        { type: 'First', params: { mode: 'two', enabled: false, retained: { custom: 1 } } },
      ],
    })
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1)
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith([])
    expect(JSON.stringify(workspace)).toBe(original)
  })

  it('should offer only ancestors and descendants and support choosing another endpoint', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={fixture()} /></TestWrapper>)
    const starts = screen.getByLabelText('Starting ancestor')
    expect(within(starts).getAllByRole('option').map((option: HTMLElement) => option.getAttribute('value')))
      .toEqual(['root', 'start', 'end'])
    await user.selectOptions(starts, 'start')
    const endpoints = screen.getByLabelText('Path endpoint')
    expect(within(endpoints).queryByRole('option', { name: /unrelated/ })).not.toBeInTheDocument()
    expect(within(endpoints).queryByRole('option', { name: /root-side/ })).not.toBeInTheDocument()
    await user.selectOptions(endpoints, 'side-leaf')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['start', 'side', 'side-leaf'])
    expect(screen.getByText('3 prompts will be copied as drafts.')).toBeVisible()
    expect(within(screen.getByRole('list', { name: 'Prompts to copy' })).queryByText('end prompt')).not.toBeInTheDocument()
    await user.selectOptions(starts, 'side')
    expect(screen.getByLabelText('First prompt')).toHaveValue('side prompt')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['side', 'side-leaf'])
  })

  it.each(['false', 'throw'])('should retain the edited form after a %s save failure and allow retry', async (failure: string) => {
    const user = userEvent.setup()
    const onFork = failure === 'false' ? jest.fn().mockResolvedValueOnce(false) : jest.fn().mockRejectedValueOnce(new Error('Save failed'))
    onFork.mockResolvedValueOnce(true)
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={fixture()} onFork={onFork} /></TestWrapper>)
    await user.type(screen.getByLabelText('First prompt'), ' edited')
    await user.click(screen.getByRole('button', { name: 'Fork path' }))
    expect(await screen.findByText(/path could not be saved/)).toBeVisible()
    expect(screen.getByRole('dialog', { name: 'Fork path' })).toBeVisible()
    expect(screen.getByLabelText('First prompt')).toHaveValue('end prompt edited')
    expect(defaultProps.onClose).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Fork path' }))
    expect(onFork).toHaveBeenCalledTimes(2)
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1)
  })

  it('should prevent double submission and closing while saving', async () => {
    const user = userEvent.setup()
    let finish: (saved: boolean) => void = () => { throw new Error('Save not started') }
    const onFork = jest.fn(() => new Promise<boolean>((resolve: (saved: boolean) => void) => { finish = resolve }))
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={fixture()} onFork={onFork} /></TestWrapper>)
    await user.dblClick(screen.getByRole('button', { name: 'Fork path' }))
    expect(onFork).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Saving path…' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Cancel', exact: true })).toBeDisabled()
    await user.keyboard('{Escape}')
    expect(defaultProps.onClose).not.toHaveBeenCalled()
    await act(async () => { finish(true) })
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1)
  })

  it('should finish the original save even when the controller selects the newly created root', async () => {
    const user = userEvent.setup()
    const workspace = fixture()
    let finish: (saved: boolean) => void = () => { throw new Error('Save not started') }
    const onFork = jest.fn(() => new Promise<boolean>((resolve: (saved: boolean) => void) => { finish = resolve }))
    const { rerender } = render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} onFork={onFork} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Fork path' }))
    const forked = applyTreeCommand(workspace, {
      type: 'forkPath', nodeId: 'end', descendantId: 'end', prompt: 'end prompt', converters: [],
    })
    rerender(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={forked}
      selectedId={forked.nodes[workspace.nodes.length].id} onFork={onFork} /></TestWrapper>)
    expect(screen.getByLabelText('Path endpoint')).toHaveValue('end')
    expect(screen.getByRole('button', { name: 'Saving path…' })).toBeDisabled()
    await act(async () => { finish(true) })
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1)
  })

  it.each(['missing', 'pruned', 'running', 'overflow'])('should disable invalid %s paths using canonical validation', (invalid: string) => {
    const workspace = fixture()
    let selectedId = 'end'
    if (invalid === 'missing') selectedId = 'missing'
    if (invalid === 'pruned') workspace.nodes[0].pruned = true
    if (invalid === 'running') {
      selectedId = 'root'
      workspace.nodes[0].status = 'running'
    }
    if (invalid === 'overflow') {
      const missing = MAX_TREE_NODES - workspace.nodes.length
      workspace.nodes.push(...Array.from({ length: missing }, (_: unknown, index: number) => node(`filler-${index}`)))
    }
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} selectedId={selectedId} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeDisabled()
    expect(screen.getByText(/Invalid conversation tree:/)).toBeVisible()
    expect(defaultProps.onFork).not.toHaveBeenCalled()
  })

  it('should block empty prompts and unapplied converter edits, including cancelled edits', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={fixture()} /></TestWrapper>)
    await user.clear(screen.getByLabelText('First prompt'))
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeDisabled()
    await user.type(screen.getByLabelText('First prompt'), 'Ready')
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Cancel converter' }))
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeEnabled()
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(screen.getByLabelText('Starting ancestor'), 'start')
    expect(screen.queryByLabelText('Converter')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Fork path' })).toBeEnabled()
  })

  it('should clear previews on cancel, close, and unmount and reset a reopened form', async () => {
    const user = userEvent.setup()
    const workspace = fixture()
    const { rerender, unmount } = render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} /></TestWrapper>)
    await user.type(screen.getByLabelText('First prompt'), ' unsaved')
    await user.click(screen.getByRole('button', { name: 'Cancel', exact: true }))
    expect(defaultProps.onClose).toHaveBeenCalledTimes(1)
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith([])
    rerender(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} open={false} /></TestWrapper>)
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith([])
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    rerender(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={workspace} /></TestWrapper>)
    expect(screen.getByLabelText('First prompt')).toHaveValue('end prompt')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['end'])
    await user.keyboard('{Escape}')
    expect(defaultProps.onClose).toHaveBeenCalledTimes(2)
    unmount()
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith([])
  })

  it('should support inline preview callbacks that update the parent without creating render loops', async () => {
    const user = userEvent.setup()
    const workspace = fixture()
    function Harness() {
      const [, setPreview] = useState<string[]>([])
      return <TreeForkPathDialog {...defaultProps} workspace={workspace}
        onPreview={(nodeIds: string[]) => { setPreview(nodeIds); defaultProps.onPreview(nodeIds) }} />
    }
    render(<TestWrapper><Harness /></TestWrapper>)
    await user.selectOptions(screen.getByLabelText('Starting ancestor'), 'start')
    expect(defaultProps.onPreview).toHaveBeenLastCalledWith(['start', 'end'])
    expect(defaultProps.onFork).not.toHaveBeenCalled()
  })

  it('should work without a preview callback and ignore completion after unmount', async () => {
    const user = userEvent.setup()
    let finish: (saved: boolean) => void = () => { throw new Error('Save not started') }
    const onFork = jest.fn((_command: Extract<TreeCommand, { type: 'forkPath' }>) =>
      new Promise<boolean>((resolve: (saved: boolean) => void) => { finish = resolve }))
    const { unmount } = render(<TestWrapper><TreeForkPathDialog {...defaultProps} workspace={fixture()}
      onPreview={undefined} onFork={onFork} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Fork path' }))
    await waitFor(() => { expect(onFork).toHaveBeenCalledTimes(1) })
    unmount()
    await act(async () => { finish(true) })
    expect(defaultProps.onClose).not.toHaveBeenCalled()
  })
})
