import type { AgentExecutionEvent } from '@/types'

import { reduceAgentEvents } from './agentActivity'

function event(sequence: number, update: Record<string, unknown>, turn = 'turn'): AgentExecutionEvent {
  return {
    execution_id: 'execution', sequence, turn_id: turn, timestamp: `2026-10-08T01:00:0${sequence}Z`,
    direction: 'incoming', payload: { method: 'session/update', params: { update } },
  }
}

describe('reduceAgentEvents', () => {
  it('preserves text/tool interleaving independently of page boundaries and replay duplicates', () => {
    const events = [
      event(1, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'First' } }),
      event(2, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: ' sentence.' } }),
      event(3, { sessionUpdate: 'tool_call', toolCallId: 'one', title: 'Read' }),
      event(4, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Between.' } }),
      event(5, { sessionUpdate: 'tool_call', toolCallId: 'two', title: 'Write' }),
      event(6, { sessionUpdate: 'tool_call_update', toolCallId: 'one', status: 'completed' }),
      event(7, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Final.' } }),
    ]
    const complete = reduceAgentEvents({}, events)
    const paged = events.reduce((state, item) => reduceAgentEvents(state, [item]), {})
    expect(paged).toEqual(complete)
    expect(reduceAgentEvents(complete, events)).toEqual(complete)
    expect(complete.turn.blocks?.map((block) => block.kind)).toEqual(['text', 'tool', 'text', 'tool', 'text'])
    expect(complete.turn.blocks?.[0]).toMatchObject({ text: 'First sentence.' })
    expect(complete.turn.tools[0].status).toBe('completed')
    expect(complete.turn.text).toBe('First sentence.Between.Final.')
  })
  it('keeps Markdown in one text block across journal gaps and non-content events', () => {
    const events: AgentExecutionEvent[] = [
      event(1, { sessionUpdate: 'agent_message_chunk', messageId: 'reply', content: { type: 'text', text: '```python\n' } }),
      { ...event(3, {}), direction: 'outgoing', payload: { method: 'session/prompt' } },
      event(5, { sessionUpdate: 'usage_update' }),
      event(8, { sessionUpdate: 'agent_message_chunk', messageId: 'reply', content: { type: 'text', text: 'print(31)\n' } }),
      { ...event(9, {}), direction: 'lifecycle', payload: { type: 'session.connected' } },
      event(12, { sessionUpdate: 'agent_message_chunk', messageId: 'reply', content: { type: 'text', text: '```' } }),
    ]
    const whole = reduceAgentEvents({}, events)
    const paged = events.reduce((state, item) => reduceAgentEvents(state, [item]), {})
    expect(paged).toEqual(whole)
    expect(whole.turn.blocks).toEqual([
      { kind: 'text', id: 'text-1', messageId: 'reply', text: '```python\nprint(31)\n```' },
    ])
    expect(reduceAgentEvents(whole, events)).toEqual(whole)
  })

  it('breaks text blocks on new messages, plans, and newly appearing tools only', () => {
    const chunk = (text: string, messageId: string) => ({
      sessionUpdate: 'agent_message_chunk', messageId, content: { type: 'text', text },
    })
    const result = reduceAgentEvents({}, [
      event(1, chunk('First.', 'a')),
      event(2, chunk('Second.', 'b')),
      event(3, { sessionUpdate: 'plan', entries: [] }),
      event(4, chunk('After plan.', 'b')),
      event(5, { sessionUpdate: 'tool_call', toolCallId: 'tool', title: 'Read' }),
      event(6, chunk('- first\n', 'b')),
      event(7, { sessionUpdate: 'tool_call_update', toolCallId: 'tool', status: 'completed' }),
      event(8, chunk('- second', 'b')),
    ])
    expect(result.turn.blocks?.map((block) => block.kind)).toEqual(['text', 'text', 'plan', 'text', 'tool', 'text'])
    expect(result.turn.blocks?.[5]).toMatchObject({ text: '- first\n- second' })
  })
  it('updates tools in place and retains omitted or null input/output fields', () => {
    const start = event(1, { sessionUpdate: 'tool_call', toolCallId: 'one', title: 'Read orders', rawInput: { file: 'orders.json' } })
    const previous = reduceAgentEvents({}, [start])
    const next = reduceAgentEvents(previous, [
      event(2, { sessionUpdate: 'tool_call_update', toolCallId: 'one', status: 'completed', rawInput: null, rawOutput: { total: 31 } }),
    ])
    expect(next.turn.tools).toHaveLength(1)
    expect(next.turn.tools[0]).toMatchObject({ title: 'Read orders', status: 'completed', rawInput: { file: 'orders.json' }, rawOutput: { total: 31 } })
    expect(next.turn.tools[0].name).toBeUndefined()
    expect(previous.turn.tools[0].status).toBe('not reported')
  })

  it('keeps turns separate, including when a tool ID is reused', () => {
    const result = reduceAgentEvents({}, [
      event(1, { sessionUpdate: 'tool_call', toolCallId: 'one', title: 'First' }),
      event(2, { sessionUpdate: 'tool_call', toolCallId: 'one', title: 'Second' }, 'other'),
      event(3, { sessionUpdate: 'agent_message_chunk', content: { type: 'text', text: 'Working' } }),
    ])
    expect(result.turn.tools[0].title).toBe('First')
    expect(result.other.tools[0].title).toBe('Second')
    expect(result.turn.text).toBe('Working')
  })

  it('does not fabricate tools for malformed or unrelated events', () => {
    expect(reduceAgentEvents({}, [
      event(1, { sessionUpdate: 'tool_call' }),
      event(2, { sessionUpdate: 'agent_thought_chunk', content: { text: 'not displayed' } }),
    ])).toEqual({})
  })
})
