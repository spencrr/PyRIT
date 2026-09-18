import type { ReactNode } from 'react'

import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'

import type { TreeAssistantTurn } from '@/types'

import { exportAssistantChat } from './treeAssistantStorage'
import TreeAssistantTurnDetails from './TreeAssistantTurnDetails'

const TURN: TreeAssistantTurn = {
  request_id: 'request', message: 'Explore', reply: 'Review the proposal.', proposals: [],
  tool_calls: [{
    id: 'tool-call', name: 'inspect_tree', arguments: { node_id: 'node', include_scores: true },
    result: 'The recorded result.', status: 'completed', duration_ms: 1.25, truncated: false,
  }],
  context_summary: {
    workspace_id: 'workspace', revision: 12, selected_node_id: 'node', node_count: 1,
    model: 'configured-model', api: 'Responses', instructions: 'Only propose changes.',
    tools: ['inspect_tree', 'propose_plan'], restored: false,
  },
  usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 },
}

function TestWrapper({ children }: { readonly children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('TreeAssistantTurnDetails', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should disclose recorded tools, context fields and token usage on demand', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeAssistantTurnDetails turn={TURN} /></TestWrapper>)
    const summary = screen.getByText('Tools (1) · context · token usage')
    expect(screen.queryByLabelText('Server instructions text')).not.toBeInTheDocument()
    expect(screen.queryByLabelText('inspect_tree argument text')).not.toBeInTheDocument()
    await user.tab()
    expect(summary).toHaveFocus()
    await user.click(summary)
    const context = screen.getByRole('region', { name: 'Turn context' })
    expect(within(context).getByText('workspace')).toBeVisible()
    expect(within(context).getByText('12')).toBeVisible()
    expect(within(context).getByText('node')).toBeVisible()
    expect(within(context).getByText('configured-model')).toBeVisible()
    expect(within(context).getByText('Responses')).toBeVisible()
    expect(within(context).getByText('propose_plan')).toBeVisible()
    expect(screen.getByLabelText('Server instructions text')).toHaveTextContent('Only propose changes.')
    await user.click(screen.getByText('inspect_tree · completed · 1.3 ms'))
    expect(screen.getByRole('region', { name: 'inspect_tree arguments' })).toBeVisible()
    expect(screen.getByLabelText('inspect_tree argument text')).toHaveTextContent('"include_scores": true')
    expect(screen.getByLabelText('inspect_tree result text')).toHaveTextContent('The recorded result.')
    const usage = screen.getByRole('region', { name: 'Token usage' })
    expect(within(usage).getByText('100')).toBeVisible()
    expect(within(usage).getByText('20')).toBeVisible()
    expect(within(usage).getByText('120')).toBeVisible()
    expect(screen.queryByText(/marked this result as truncated/)).not.toBeInTheDocument()
  })

  it.each([
    { duration: 3.3614999993005767, label: '3.4 ms' },
    { duration: 9.99, label: '10 ms' },
    { duration: 3, label: '3 ms' },
    { duration: 0, label: '0 ms' },
    { duration: 0.04, label: '0 ms' },
  ])('should display $duration as $label without changing exported precision', async ({ duration, label }: { duration: number; label: string }) => {
    const user = userEvent.setup()
    const turn: TreeAssistantTurn = {
      ...TURN, tool_calls: [{
        id: 'tool', name: 'inspect_tree', arguments: {}, result: 'Recorded result.',
        status: 'completed', duration_ms: duration, truncated: false,
      }],
    }
    render(<TestWrapper><TreeAssistantTurnDetails turn={turn} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    await user.click(screen.getByText(`inspect_tree · completed · ${label}`))
    expect(screen.getByText(label, { exact: true })).toBeVisible()
    const exported = JSON.parse(exportAssistantChat({
      schemaVersion: 1, revision: 0, workspaceId: 'workspace', savedAt: '2026-09-17T12:00:00Z',
      session: { session_id: 'private-server-capability', workspace_id: 'workspace', model: 'configured-model', turns: [turn] },
      draft: '', pendingMessage: null, unreported: null, executing: null,
    }))
    expect(exported.session.turns[0].tool_calls[0].duration_ms).toBe(duration)
  })

  it('should show all recorded tool text and explicitly identify server truncation', async () => {
    const user = userEvent.setup()
    const result = `${'Full result text. '.repeat(2500)}FINAL RECORDED LINE`
    const turn: TreeAssistantTurn = {
      ...TURN, tool_calls: [{
        id: 'tool-call', name: 'inspect_tree', arguments: { prompt: 'Complete arguments'.repeat(1000) },
        result, status: 'error', duration_ms: 0, truncated: true,
      }],
    }
    render(<TestWrapper><TreeAssistantTurnDetails turn={turn} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    await user.click(screen.getByText('inspect_tree · error · 0 ms'))
    expect(screen.getByText(/server marked this result as truncated/i)).toBeVisible()
    expect(screen.getByLabelText('inspect_tree result text').textContent).toBe(result)
    expect(screen.getByLabelText('inspect_tree argument text').textContent).toBe(JSON.stringify(turn.tool_calls?.[0].arguments, null, 2))
    expect(screen.getByLabelText('inspect_tree result text')).toHaveAttribute('tabindex', '0')
  })

  it('should render instructions and tool results as inert plain text', async () => {
    const user = userEvent.setup()
    const instructions = '<button>Run now</button><script>window.executed = true</script>'
    const turn: TreeAssistantTurn = {
      ...TURN,
      context_summary: {
        workspace_id: 'workspace', revision: 0, selected_node_id: null, node_count: 0,
        model: 'model', api: 'Responses', tools: [], restored: true, instructions,
      },
      tool_calls: [{
        id: 'tool', name: 'inspect_tree', arguments: {},
        result: '<img src="invalid" onerror="window.executed = true">', status: 'completed', duration_ms: 0, truncated: false,
      }],
    }
    render(<TestWrapper><TreeAssistantTurnDetails turn={turn} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    await user.click(screen.getByText('inspect_tree · completed · 0 ms'))
    expect(screen.getByLabelText('Server instructions text').textContent).toBe(instructions)
    expect(screen.getByText('No tools listed.')).toBeVisible()
    expect(screen.getByText('None')).toBeVisible()
    expect(screen.getByText('Yes')).toBeVisible()
    expect(screen.queryByRole('button', { name: 'Run now' })).not.toBeInTheDocument()
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })

  it('should identify unavailable legacy trace without inventing tools, timings or model metrics', async () => {
    const user = userEvent.setup()
    const turn: TreeAssistantTurn = { request_id: 'legacy', message: 'Question', reply: 'Answer', proposals: [] }
    render(<TestWrapper><TreeAssistantTurnDetails turn={turn} /></TestWrapper>)
    await user.click(screen.getByText('Turn details'))
    expect(screen.getByText(/tool trace unavailable.*older turns/i)).toBeVisible()
    expect(screen.getByText(/context summary unavailable/i)).toBeVisible()
    expect(screen.queryByText(/Tools \(0\)/)).not.toBeInTheDocument()
    expect(screen.queryByText('Duration')).not.toBeInTheDocument()
    expect(screen.queryByText('Model')).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Token usage' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Restoration notice' })).not.toBeInTheDocument()
  })

  it('should show the complete supplied restoration notice as keyboard-accessible inert text', async () => {
    const user = userEvent.setup()
    const context = TURN.context_summary
    if (!context) throw new Error('Missing test context summary')
    const notice = `${'Only bounded historical context was restored. '.repeat(40)}\n<button>Resume actions</button>\nEND OF NOTICE`
    const turn: TreeAssistantTurn = { ...TURN, context_summary: { ...context, restored: true, restoration_notice: notice } }
    render(<TestWrapper><TreeAssistantTurnDetails turn={turn} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    expect(screen.getByRole('region', { name: 'Restoration notice' })).toBeVisible()
    const text = screen.getByLabelText('Restoration notice text')
    expect(text.textContent).toBe(notice)
    await user.click(text)
    expect(text).toHaveFocus()
    expect(screen.queryByRole('button', { name: 'Resume actions' })).not.toBeInTheDocument()
  })

  it('should not invent a restoration notice when restored context does not provide one', async () => {
    const user = userEvent.setup()
    const context = TURN.context_summary
    if (!context) throw new Error('Missing test context summary')
    render(<TestWrapper><TreeAssistantTurnDetails turn={{ ...TURN, context_summary: { ...context, restored: true } }} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    expect(screen.getByText('Yes')).toBeVisible()
    expect(screen.queryByRole('region', { name: 'Restoration notice' })).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Restoration notice text')).not.toBeInTheDocument()
  })

  it('should distinguish an empty recorded tool list from an unavailable trace', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeAssistantTurnDetails turn={{ ...TURN, tool_calls: [], context_summary: null, usage: {} }} /></TestWrapper>)
    await user.click(screen.getByText('Tools (0)'))
    expect(screen.getByText('No tool calls were recorded for this turn.')).toBeVisible()
    expect(screen.queryByText(/tool trace unavailable/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Token usage' })).not.toBeInTheDocument()
  })

  it('should show only supplied usage fields, retaining explicit zero without calculating missing totals', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><TreeAssistantTurnDetails turn={{ ...TURN, usage: { input_tokens: 0 } }} /></TestWrapper>)
    await user.click(screen.getByText('Tools (1) · context · token usage'))
    const usage = screen.getByRole('region', { name: 'Token usage' })
    expect(within(usage).getByText('Input tokens')).toBeVisible()
    expect(within(usage).getByText('0')).toBeVisible()
    expect(within(usage).queryByText('Output tokens')).not.toBeInTheDocument()
    expect(within(usage).queryByText('Total tokens')).not.toBeInTheDocument()
  })
})
