import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ConversationExecution } from '@/types'

import AgentExecutionStatus from './AgentExecutionStatus'

describe('AgentExecutionStatus', () => {
  it('distinguishes feed, session and environment and offers explicit held actions', async () => {
    const onControl = jest.fn().mockResolvedValue(undefined)
    const execution: ConversationExecution = {
      id: 'one', conversation_id: 'one', state: 'held', environment: 'docker', model: '', turns: [],
      capture_error: null, close_reason: null, source_coverage: 'ACP only', artifacts: [], event_count: 1,
      connection_state: 'ready', interactive: true, held_until: new Date(Date.now() + 60000).toISOString(),
    }
    render(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={execution} feed="reconnecting" disabled={false} cancelling={false}
        onCancel={jest.fn()} onControl={onControl} />
    </FluentProvider>)
    expect(screen.getByText('Feed: reconnecting')).toBeInTheDocument()
    expect(screen.getByText('ACP: ready')).toBeInTheDocument()
    expect(screen.getByText('Environment: docker / held')).toBeInTheDocument()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Continue', exact: true }))
    expect(onControl).toHaveBeenCalledWith('continue')
  })
})
