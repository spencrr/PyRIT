import type { AgentCheckpoint, AgentCheckpointStatus, AgentEventPage, AgentEvidenceSnapshot, AgentExecution, ConversationExecution } from '@/types'
import { apiClient, openApiEventStream } from './api'

export const executionsApi = {
  async checkpoint(id: string): Promise<AgentCheckpointStatus> {
    return (await apiClient.get<AgentCheckpointStatus>(`/executions/${encodeURIComponent(id)}/checkpoint`)).data
  },
  async saveCheckpoint(id: string): Promise<AgentCheckpoint> {
    return (await apiClient.post<AgentCheckpoint>(`/executions/${encodeURIComponent(id)}/checkpoint`, {
      acknowledge_sensitive_state: true,
    })).data
  },
  async restoreCheckpoint(id: string): Promise<AgentExecution> {
    return (await apiClient.post<AgentExecution>(`/executions/${encodeURIComponent(id)}/checkpoint/restore`)).data
  },
  async deleteCheckpoint(id: string): Promise<AgentCheckpointStatus> {
    return (await apiClient.delete<AgentCheckpointStatus>(`/executions/${encodeURIComponent(id)}/checkpoint`)).data
  },
  async evidence(id: string): Promise<AgentEvidenceSnapshot> {
    return (await apiClient.get<AgentEvidenceSnapshot>(`/executions/${encodeURIComponent(id)}/evidence`)).data
  },
  async downloadEvidence(id: string): Promise<Blob> {
    return (await apiClient.get<Blob>(`/executions/${encodeURIComponent(id)}/evidence.zip`, { responseType: 'blob' })).data
  },
  stream(attackId: string, conversationId: string, executionId: string | null, after: number, signal: AbortSignal): Promise<Response> {
    const query = new URLSearchParams({ after: String(after) })
    if (executionId) query.set('execution_id', executionId)
    return openApiEventStream(`${conversationPath(attackId, conversationId)}/stream?${query}`, signal)
  },
  async permission(attackId: string, conversationId: string, executionId: string, approvalId: string, allow: boolean): Promise<ConversationExecution | null> {
    return (await apiClient.post<ConversationExecution | null>(
      `${conversationPath(attackId, conversationId)}/${executionId}/permissions/${approvalId}`, { allow },
    )).data
  },
  async control(attackId: string, conversationId: string, executionId: string, action: 'continue' | 'extend' | 'close'): Promise<ConversationExecution | null> {
    return (await apiClient.post<ConversationExecution | null>(
      `${conversationPath(attackId, conversationId)}/${executionId}/control/${action}`,
    )).data
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
