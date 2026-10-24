import type { AgentEventPage, AgentExecution, ConversationExecution } from '@/types'
import { apiClient, openApiEventStream } from './api'

export const executionsApi = {
  stream(attackId: string, conversationId: string, executionId: string | null, after: number, signal: AbortSignal): Promise<Response> {
    const query = new URLSearchParams({ after: String(after) })
    if (executionId) query.set('execution_id', executionId)
    return openApiEventStream(`${conversationPath(attackId, conversationId)}/stream?${query}`, signal)
  },
  async conversation(attackId: string, conversationId: string): Promise<ConversationExecution | null> {
    return (await apiClient.get<ConversationExecution | null>(conversationPath(attackId, conversationId))).data
  },
  async conversationEvents(attackId: string, conversationId: string, id: string, after: number): Promise<AgentEventPage> {
    return (await apiClient.get<AgentEventPage>(`${conversationPath(attackId, conversationId)}/${encodeURIComponent(id)}/events`, {
      params: { after, limit: 100 },
    })).data
  },
  async cancelConversation(attackId: string, conversationId: string, id: string): Promise<ConversationExecution | null> {
    return (await apiClient.post<ConversationExecution | null>(`${conversationPath(attackId, conversationId)}/${encodeURIComponent(id)}/cancel`)).data
  },
  async list(offset = 0, conversationId?: string): Promise<AgentExecution[]> {
    return (await apiClient.get<AgentExecution[]>('/executions', {
      params: { offset, limit: 50, conversation_id: conversationId },
    })).data
  },
  async get(id: string): Promise<AgentExecution> {
    return (await apiClient.get<AgentExecution>(`/executions/${encodeURIComponent(id)}`)).data
  },
  async events(id: string, after: number): Promise<AgentEventPage> {
    return (await apiClient.get<AgentEventPage>(`/executions/${encodeURIComponent(id)}/events`, {
      params: { after, limit: 100 },
    })).data
  },
  async cancel(id: string): Promise<AgentExecution> {
    return (await apiClient.post<AgentExecution>(`/executions/${encodeURIComponent(id)}/cancel`)).data
  },
  async close(id: string): Promise<AgentExecution> {
    return (await apiClient.post<AgentExecution>(`/executions/${encodeURIComponent(id)}/close`)).data
  },
}

function conversationPath(attackId: string, conversationId: string): string {
  return `/attacks/${encodeURIComponent(attackId)}/conversations/${encodeURIComponent(conversationId)}/execution`
}
