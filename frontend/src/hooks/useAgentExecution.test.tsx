import { act, renderHook, waitFor } from '@testing-library/react'

import { executionsApi } from '@/services/executions'
import { consumeEventStream } from '@/services/eventStream'
import type { ConversationExecution } from '@/types'

import { useAgentExecution } from './useAgentExecution'

jest.mock('@/services/executions', () => ({ executionsApi: {
  stream: jest.fn(), cancelConversation: jest.fn(),
} }))
jest.mock('@/services/eventStream', () => ({ consumeEventStream: jest.fn() }))

const execution: ConversationExecution = {
  id: 'exec', conversation_id: 'one', state: 'working', environment: 'docker', model: 'test', turns: [],
  capture_error: null, close_reason: null, source_coverage: 'ACP only', artifacts: [], event_count: 1,
}

describe('useAgentExecution', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(executionsApi.stream).mockResolvedValue({} as Response)
    jest.mocked(consumeEventStream).mockImplementation(async (_response, onFrame) => {
      await onFrame({ event: 'reset', data: JSON.stringify({ execution_id: 'exec' }) })
      await onFrame({ event: 'state', data: JSON.stringify(execution) })
      await onFrame({ event: 'events', data: JSON.stringify({ execution_id: 'exec', events: [], next_cursor: 1 }) })
      await onFrame({ event: 'end', data: '{}' })
    })
  })

  it('makes no execution requests for a model-only target', () => {
    renderHook(() => useAgentExecution('attack', 'one', false))
    expect(executionsApi.stream).not.toHaveBeenCalled()
  })

  it('cancels via the conversation boundary without calling the admin API', async () => {
    jest.mocked(executionsApi.cancelConversation).mockResolvedValue({ ...execution, state: 'idle' })
    const { result } = renderHook(() => useAgentExecution('attack', 'one', true))
    await waitFor(() => expect(result.current.execution?.id).toBe('exec'))
    await act(async () => { await result.current.cancel() })
    expect(executionsApi.cancelConversation).toHaveBeenCalledWith('attack', 'one', 'exec')
    expect(result.current.execution?.state).toBe('idle')
  })

  it('does not show the previous conversation when selection changes', async () => {
    const { result, rerender } = renderHook(({ conversation }) => useAgentExecution('attack', conversation, true), {
      initialProps: { conversation: 'one' },
    })
    await waitFor(() => expect(result.current.execution?.id).toBe('exec'))
    jest.mocked(executionsApi.stream).mockReturnValue(new Promise(() => {}))
    rerender({ conversation: 'two' })
    expect(result.current.execution).toBeNull()
    expect(result.current.turns).toEqual({})
  })

  it('aborts the observer connection on unmount without cancelling the turn', async () => {
    const { result, unmount } = renderHook(() => useAgentExecution('attack', 'one', true))
    await waitFor(() => expect(result.current.execution?.id).toBe('exec'))
    const signal = jest.mocked(executionsApi.stream).mock.calls[0][4]
    unmount()
    expect(signal.aborted).toBe(true)
    expect(executionsApi.cancelConversation).not.toHaveBeenCalled()
  })
})
