import type { AgentExecutionEvent, AgentToolActivity, AgentTurnActivity } from '@/types'

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export function reduceAgentEvents(
  current: Record<string, AgentTurnActivity>, events: AgentExecutionEvent[],
): Record<string, AgentTurnActivity> {
  const next = { ...current }
  for (const event of events) {
    if (event.turn_id && event.sequence <= (next[event.turn_id]?.lastSequence ?? 0)) continue
    if (!event.turn_id || event.direction !== 'incoming' || !isObject(event.payload.params)) continue
    const params = event.payload.params
    const update = params.update
    if (!isObject(update)) continue
    const turn = next[event.turn_id] ?? { text: '', tools: [] }
    if (update.sessionUpdate === 'agent_message_chunk' && isObject(update.content) && typeof update.content.text === 'string') {
      const blocks = [...turn.blocks ?? []]
      const last = blocks[blocks.length - 1]
      const messageId = typeof update.messageId === 'string' ? update.messageId : undefined
      if (last?.kind === 'text' && last.messageId === messageId) {
        blocks[blocks.length - 1] = { ...last, text: last.text + update.content.text }
      } else {
        blocks.push({ kind: 'text', id: `text-${event.sequence}`, text: update.content.text, messageId })
      }
      next[event.turn_id] = {
        ...turn, text: turn.text + update.content.text, blocks,
        lastSequence: event.sequence,
      }
      continue
    }
    if (update.sessionUpdate === 'plan') {
      next[event.turn_id] = {
        ...turn, lastSequence: event.sequence,
        blocks: [...turn.blocks ?? [], { kind: 'plan', id: `plan-${event.sequence}`, entries: update.entries }],
      }
      continue
    }
    if (!['tool_call', 'tool_call_update'].includes(String(update.sessionUpdate)) || typeof update.toolCallId !== 'string') continue
    const previous = turn.tools.find((tool: AgentToolActivity) => tool.id === update.toolCallId)
    const tool: AgentToolActivity = {
      ...previous,
      id: update.toolCallId,
      title: typeof update.title === 'string' ? update.title : previous?.title ?? 'Tool call (title not reported)',
      status: typeof update.status === 'string' ? update.status : previous?.status ?? 'not reported',
      firstSeen: previous?.firstSeen ?? event.timestamp,
      lastSeen: event.timestamp,
      name: typeof update.name === 'string' ? update.name : previous?.name,
      kind: typeof update.kind === 'string' ? update.kind : previous?.kind,
      rawInput: update.rawInput ?? previous?.rawInput,
      rawOutput: update.rawOutput ?? previous?.rawOutput,
      content: update.content ?? previous?.content,
      locations: update.locations ?? previous?.locations,
    }
    next[event.turn_id] = {
      ...turn, lastSequence: event.sequence,
      tools: previous ? turn.tools.map((item: AgentToolActivity) => item.id === tool.id ? tool : item) : [...turn.tools, tool],
      blocks: previous ? turn.blocks : [...turn.blocks ?? [], { kind: 'tool', id: `tool-${event.sequence}`, toolId: tool.id }],
    }
  }
  return next
}
