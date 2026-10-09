import { useCallback, useEffect, useEffectEvent, useState } from 'react'

import { executionsApi } from '@/services/executions'
import { consumeEventStream } from '@/services/eventStream'
import { toApiError } from '@/services/errors'
import { reduceAgentEvents } from '@/utils/agentActivity'
import type { AgentEventPage, AgentTurnActivity, ConversationExecution } from '@/types'

interface ExecutionSnapshot {
  readonly key: string
  readonly execution: ConversationExecution | null
  readonly turns: Record<string, AgentTurnActivity>
  readonly error: string | null
  readonly feed?: 'connecting' | 'live' | 'reconnecting' | 'closed'
}

export function useAgentExecution(
  attackId: string | null, conversationId: string | null, enabled: boolean,
  onObserved?: (execution: ConversationExecution) => Promise<void>,
) {
  const key = `${attackId}:${conversationId}`
  const [snapshot, setSnapshot] = useState<ExecutionSnapshot | null>(null)
  const [cancellingKey, setCancellingKey] = useState<string | null>(null)
  const [actionError, setActionError] = useState<{ key: string; message: string } | null>(null)
  const observed = useEffectEvent(async (execution: ConversationExecution): Promise<void> => {
    await onObserved?.(execution)
  })

  useEffect(() => {
    if (!enabled || !attackId || !conversationId) return
    let stopped = false
    let cursor = 0
    let executionId: string | null = null
    let turns: Record<string, AgentTurnActivity> = {}
    let execution: ConversationExecution | null = null
    let reconnectDelay = 1000
    let timer: ReturnType<typeof setTimeout>
    const controller = new AbortController()
    const connect = async (): Promise<void> => {
      let ended = false
      try {
        const response = await executionsApi.stream(attackId, conversationId, executionId, cursor, controller.signal)
        reconnectDelay = 1000
        await consumeEventStream(response, async (frame): Promise<void> => {
          if (stopped) return
          if (frame.event === 'reset') {
            const reset: { execution_id: string } = JSON.parse(frame.data)
            executionId = reset.execution_id; cursor = 0; turns = {}
          } else if (frame.event === 'state') {
            execution = JSON.parse(frame.data)
            if (execution) executionId = execution.id
          } else if (frame.event === 'events') {
            const page: AgentEventPage & { execution_id: string } = JSON.parse(frame.data)
            if (page.execution_id !== executionId) throw new Error('Execution stream identity changed without reset')
            turns = reduceAgentEvents(turns, page.events)
            cursor = page.next_cursor
          } else if (frame.event === 'end') {
            ended = true
          }
          setSnapshot({ key, execution, turns, error: null, feed: ended ? 'closed' : 'live' })
          if (execution && frame.event === 'state') await observed(execution)
        })
        if (!stopped && !ended) setSnapshot({ key, execution, turns, error: null, feed: 'reconnecting' })
      } catch (cause: unknown) {
        if (!stopped) setSnapshot({ key, execution, turns, error: toApiError(cause).detail, feed: 'reconnecting' })
        reconnectDelay = Math.min(reconnectDelay * 2, 15000)
      } finally {
        if (!stopped && !ended) timer = setTimeout(() => { void connect() }, reconnectDelay)
      }
    }
    void connect()
    return () => { stopped = true; controller.abort(); clearTimeout(timer) }
  }, [attackId, conversationId, enabled, key])

  const visible = enabled && snapshot?.key === key ? snapshot : null
  const execution = visible?.execution ?? null
  const cancel = useCallback(async (): Promise<void> => {
    if (!attackId || !conversationId || !execution) return
    setCancellingKey(key)
    setActionError(null)
    try {
      const updated = await executionsApi.cancelConversation(attackId, conversationId, execution.id)
      setSnapshot((previous: ExecutionSnapshot | null) => previous?.key === key
        ? { ...previous, execution: updated, error: null } : previous)
    } catch (cause: unknown) {
      setActionError({ key, message: toApiError(cause).detail })
    } finally {
      setCancellingKey((previous: string | null) => previous === key ? null : previous)
    }
  }, [attackId, conversationId, execution, key])

  return {
    execution, turns: visible?.turns ?? {},
    feed: visible?.feed ?? 'connecting',
    error: actionError?.key === key ? actionError.message : visible?.error ?? null,
    cancelling: cancellingKey === key, cancel,
  }
}
