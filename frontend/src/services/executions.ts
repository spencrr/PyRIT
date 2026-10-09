import type { AgentEventPage, AgentExecution } from '@/types'
import { apiClient } from './api'

export const executionsApi = {
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
