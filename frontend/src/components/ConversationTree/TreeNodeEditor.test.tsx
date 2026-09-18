import type { ComponentProps, ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import { makeTarget } from '@/test-utils/targetFixtures'
import type { ConverterCatalogEntry, TreeNode } from '@/types'

import NodeEditor from './TreeNodeEditor'
import { DEFAULT_TREE_SETTINGS } from './treeModel'

function TreeNodeEditor(props: Omit<ComponentProps<typeof NodeEditor>, 'settings' | 'canRun' | 'onMarkdownChange' | 'onScore'>) {
  return <NodeEditor {...props} settings={DEFAULT_TREE_SETTINGS} canRun onMarkdownChange={jest.fn()} onScore={jest.fn()} />
}

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const NODE: TreeNode = {
  id: 'root', parentId: null, prompt: 'A harmless test', converters: [],
  status: 'draft', kept: false, pruned: false,
}
const CATALOG: ConverterCatalogEntry[] = [{
  converter_type: 'StringJoinConverter', supported_input_types: ['text'], supported_output_types: ['text'],
  is_llm_based: false, parameters: [{ name: 'join_value', required: false, type_name: 'str' }],
}]

describe('TreeNodeEditor', () => {
  beforeEach(() => jest.clearAllMocks())

  it('stages ten samples as a distinct operation without a variant multiplier', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    const onRun = jest.fn()
    render(<TestWrapper><TreeNodeEditor node={NODE} hidden={false} catalog={CATALOG}
      disabled={false} onCommand={onCommand} onRun={onRun} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Sample again' }))
    expect(screen.queryByLabelText(/samples per variant/i)).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: '10 additional attempts' }))
    await user.click(screen.getByRole('button', { name: 'Add 10 samples' }))
    expect(onRun).not.toHaveBeenCalled()
    expect(onCommand).toHaveBeenCalledWith({ type: 'sample', nodeId: NODE.id, count: 10 })
  })

  it('creates independent child prompts with add, duplicate, edit and remove controls', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    render(<TestWrapper><TreeNodeEditor node={NODE} hidden={false} catalog={CATALOG}
      disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    await user.type(screen.getByLabelText('Child prompt 1'), 'First child\nwith context')
    await user.click(screen.getByRole('button', { name: 'Duplicate child prompt 1' }))
    await user.clear(screen.getByLabelText('Child prompt 2'))
    await user.type(screen.getByLabelText('Child prompt 2'), 'Second child')
    await user.click(screen.getByRole('button', { name: 'Add prompt variant' }))
    await user.click(screen.getByRole('button', { name: 'Remove child prompt 3' }))
    await user.click(screen.getByRole('button', { name: 'Add 2 children' }))
    expect(onCommand).toHaveBeenCalledWith({
      type: 'childVariants', nodeId: NODE.id,
      variants: [{ prompt: 'First child\nwith context', converters: [] }, { prompt: 'Second child', converters: [] }],
    })
  })

  it('compares full editable pipelines on a shared child prompt, without modifying the parent', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    render(<TestWrapper><TreeNodeEditor node={NODE} hidden={false} catalog={CATALOG}
      disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('tab', { name: 'Pipelines' }))
    await user.type(screen.getByLabelText('Shared child prompt'), 'Follow-up')
    const first = within(screen.getByRole('region', { name: 'Pipeline 1' }))
    await user.click(first.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(first.getByLabelText('Converter'), 'StringJoinConverter')
    await user.type(first.getByLabelText('join_value'), '-')
    await user.click(first.getByRole('button', { name: 'Add converter', exact: true }))
    await user.click(first.getByRole('button', { name: 'Edit converter 1' }))
    await user.clear(first.getByLabelText('join_value'))
    await user.type(first.getByLabelText('join_value'), '+')
    await user.click(first.getByRole('button', { name: 'Apply converter changes' }))
    await user.click(screen.getByRole('button', { name: 'Duplicate pipeline 1' }))
    await user.click(screen.getByRole('button', { name: 'Remove pipeline 2' }))
    await user.click(screen.getByRole('button', { name: 'Add 2 children' }))
    expect(onCommand).toHaveBeenCalledWith({
      type: 'childVariants', nodeId: NODE.id, variants: Array.from({ length: 2 }, () => ({
        prompt: 'Follow-up', converters: [{ type: 'StringJoinConverter', params: { join_value: '+' } }],
      })),
    })
    expect(NODE.converters).toEqual([])
  })

  it('offers node and subtree retries for a failed response without rewriting evidence', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    render(<TestWrapper><TreeNodeEditor node={{ ...NODE, status: 'error', error: 'Provider unavailable' }}
      hasChildren hidden={false} catalog={CATALOG} disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('heading', { name: 'Response' })).toBeVisible()
    expect(screen.queryByRole('heading', { name: /turn|observed exchange/i })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run subtree' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Retry response' }))
    await user.click(screen.getByRole('button', { name: 'Retry subtree' }))
    expect(onCommand.mock.calls).toEqual([
      [{ type: 'retry', nodeId: NODE.id, scope: 'node' }],
      [{ type: 'retry', nodeId: NODE.id, scope: 'subtree' }],
    ])
  })

  it('requires recovery of a running node instead of enabling an unsafe retry', () => {
    render(<TestWrapper><TreeNodeEditor node={{ ...NODE, status: 'running', attackResultId: 'attack', conversationId: 'conversation' }}
      hidden={false} catalog={CATALOG} disabled={false} onCommand={jest.fn()} onRun={jest.fn()} onRecover={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Retry response' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Recover recorded result' })).toBeEnabled()
  })

  it('rejects invalid sample counts and blank prompt cards without discarding drafts', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn()
    render(<TestWrapper><TreeNodeEditor node={NODE} hidden={false} catalog={CATALOG}
      disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('button', { name: 'Add 1 child' }))
    expect(screen.getByText('Enter a prompt for every child branch.')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Sample again' }))
    await user.clear(screen.getByLabelText('Additional attempts'))
    await user.type(screen.getByLabelText('Additional attempts'), '21')
    expect(screen.getByRole('button', { name: 'Add samples' })).toBeDisabled()
    expect(screen.getByText('Choose 1-20 attempts.')).toBeInTheDocument()
    expect(onCommand).not.toHaveBeenCalled()
  })

  it('stages LLM pipeline rewrites using a registered target, not credentials', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    const target = makeTarget({
      target_registry_name: 'rewrite-target',
      capabilities: {
        supports_multi_turn: true, supports_editable_history: true, supports_system_prompt: true,
        supports_json_schema: false, supports_json_output: false,
        supported_input_modalities: ['text'], supported_output_modalities: ['text'],
      },
    })
    render(<TestWrapper><TreeNodeEditor node={NODE} hidden={false}
      catalog={[{ ...CATALOG[0], converter_type: 'LLMGenericTextConverter', is_llm_based: true, parameters: [] }]}
      targets={[target]} disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByRole('tab', { name: 'Pipelines' }))
    await user.type(screen.getByLabelText('Shared child prompt'), 'Follow-up')
    const first = within(screen.getByRole('region', { name: 'Pipeline 1' }))
    await user.click(first.getByRole('button', { name: 'Add converter step' }))
    await user.selectOptions(first.getByLabelText('Converter'), 'LLMGenericTextConverter')
    await user.selectOptions(first.getByLabelText('Rewrite target'), 'rewrite-target')
    await user.click(first.getByRole('button', { name: 'Add converter', exact: true }))
    await user.click(screen.getByRole('button', { name: 'Add 2 children' }))
    expect(onCommand.mock.calls[0][0].variants[0].converters).toEqual([
      { type: 'LLMGenericTextConverter', params: { converter_target: 'rewrite-target' } },
    ])
  })

  it('requires a fork for historical edits and tracks unapplied converter changes', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    const onDirtyChange = jest.fn()
    render(<TestWrapper><TreeNodeEditor node={{ ...NODE, status: 'completed' }} hidden={false}
      catalog={CATALOG} disabled={false} onCommand={onCommand} onRun={jest.fn()} onDirtyChange={onDirtyChange} /></TestWrapper>)
    await user.clear(screen.getByLabelText('Prompt'))
    await user.type(screen.getByLabelText('Prompt'), 'Revised test')
    expect(screen.getByRole('button', { name: 'Run subtree' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Fork & cascade' }))
    expect(onCommand).toHaveBeenCalledWith({ type: 'fork', nodeId: NODE.id, prompt: 'Revised test', converters: [] })
    await user.click(screen.getByText('Prompt pipeline (none)'))
    await user.click(screen.getByRole('button', { name: 'Add converter step' }))
    expect(onDirtyChange).toHaveBeenLastCalledWith(true)
    expect(screen.getByRole('button', { name: 'Fork & cascade' })).toBeDisabled()
  })

  it('shows the recorded final sent payload above response even when a multi-step pipeline returns the original', () => {
    const sent: TreeNode = {
      ...NODE, status: 'completed',
      converters: [{ type: 'ROT13Converter', params: {} }, { type: 'ROT13Converter', params: {} }],
      messages: [
        { role: 'user', turn_number: 0, created_at: '2026-09-14T12:00:00Z', message_pieces: [{
          id: 'user-piece', original_value_data_type: 'text', converted_value_data_type: 'text', original_value: NODE.prompt,
          converted_value: NODE.prompt, scores: [], response_error: 'none',
        }] },
        { role: 'assistant', turn_number: 1, created_at: '2026-09-14T12:00:01Z', message_pieces: [{
          id: 'response-piece', original_value_data_type: 'text', converted_value_data_type: 'text',
          converted_value: 'Recorded response', scores: [], response_error: 'none',
        }] },
      ],
    }
    render(<TestWrapper><TreeNodeEditor node={sent} hidden={false} catalog={CATALOG} disabled={false} onCommand={jest.fn()} onRun={jest.fn()} /></TestWrapper>)
    const payload = screen.getByText('Sent prompt').closest('details')
    expect(payload).toHaveTextContent(NODE.prompt)
    expect(payload?.compareDocumentPosition(screen.getByRole('heading', { name: 'Response' })) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(screen.getByText('Recorded response')).toBeVisible()
  })

  it('omits sent prompt when there is no converter and exposes retries in the same logical node', async () => {
    const user = userEvent.setup()
    const onCommand = jest.fn().mockResolvedValue(true)
    render(<TestWrapper><TreeNodeEditor node={{ ...NODE, status: 'error', error: 'Failed' }} hidden={false}
      catalog={CATALOG} disabled={false} onCommand={onCommand} onRun={jest.fn()} /></TestWrapper>)
    expect(screen.queryByText('Sent prompt')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry response' }))
    expect(onCommand).toHaveBeenCalledWith({ type: 'retry', nodeId: NODE.id, scope: 'node' })
  })
})
