import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import type { ConversationExecution } from '@/types'

import AgentExecutionStatus from './AgentExecutionStatus'

describe('AgentExecutionStatus', () => {
  it('distinguishes feed, session and environment and cancels an active turn', async () => {
    const user = userEvent.setup()
    const onCancel = jest.fn().mockResolvedValue(undefined)
    const execution: ConversationExecution = {
      id: 'one', conversation_id: 'one', state: 'working', environment: 'docker', model: '', turns: [],
      capture_error: null, close_reason: null, source_coverage: 'ACP only', artifacts: [], event_count: 1,
      connection_state: 'ready',
    }
    render(<FluentProvider theme={webLightTheme}>
      <AgentExecutionStatus execution={execution} feed="reconnecting" disabled={false} cancelling={false}
        onCancel={onCancel} />
    </FluentProvider>)
    expect(screen.getByText('Feed: reconnecting')).toBeInTheDocument()
    expect(screen.getByText('ACP: ready')).toBeInTheDocument()
    expect(screen.getByText('Environment: docker / working')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel turn' }))
    expect(onCancel).toHaveBeenCalledTimes(1)
  })
})
