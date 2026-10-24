import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ConversationExecution } from '@/types'

import AgentExecutionStatus from './AgentExecutionStatus'

describe('AgentExecutionStatus', () => {
  const execution: ConversationExecution = {
    id: 'one', conversation_id: 'one', state: 'working', environment: 'docker', model: '', turns: [],
    capture_error: null, close_reason: null, source_coverage: 'ACP only', artifacts: [], event_count: 1,
    connection_state: 'ready',
  }

  it('distinguishes feed, session and environment and cancels only the running turn', async () => {
    const onCancel = jest.fn().mockResolvedValue(undefined)
    render(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={execution} feed="reconnecting" disabled={false} cancelling={false} onCancel={onCancel} />
    </FluentProvider>)
    expect(screen.getByText('Feed: reconnecting')).toBeInTheDocument()
    expect(screen.getByText('ACP: ready')).toBeInTheDocument()
    expect(screen.getByText('Environment: docker / working')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Cancel turn' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })

  it('keeps cancellation read-only while locked and hides it once the turn settles', () => {
    const { rerender } = render(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={execution} feed="live" disabled cancelling={false} onCancel={jest.fn()} />
    </FluentProvider>)
    expect(screen.getByRole('button', { name: 'Cancel turn' })).toBeDisabled()
    rerender(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={{ ...execution, state: 'idle' }} feed="live" disabled={false} cancelling={false} onCancel={jest.fn()} />
    </FluentProvider>)
    expect(screen.queryByRole('button', { name: 'Cancel turn' })).not.toBeInTheDocument()
    expect(screen.getByText('Environment: docker / idle')).toBeInTheDocument()
  })

  it('describes an unstarted execution without inventing connection state', () => {
    render(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={null} feed="idle" disabled={false} cancelling={false} onCancel={jest.fn()} />
    </FluentProvider>)
    expect(screen.getByText('ACP: not started')).toBeInTheDocument()
    expect(screen.getByText('Environment: not provisioned')).toBeInTheDocument()
    expect(screen.getByText('Saving a configuration does not start the harness.')).toBeInTheDocument()
  })
})
