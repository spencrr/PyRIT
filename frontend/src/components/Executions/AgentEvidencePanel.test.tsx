import type { ReactNode } from 'react'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { executionsApi } from '@/services/executions'
import { downloadBlob } from '@/utils/conversationExport'
import type { AgentEvidenceSnapshot } from '@/types'
import AgentEvidencePanel from './AgentEvidencePanel'

jest.mock('@/services/executions', () => ({
  executionsApi: { evidence: jest.fn(), downloadEvidence: jest.fn() },
}))
jest.mock('@/utils/conversationExport', () => ({ downloadBlob: jest.fn() }))

function Wrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function recording(): AgentEvidenceSnapshot {
  const operation = { status: 'unavailable' as const, detail: 'No checkpoint captured' }
  return {
    format_version: 1, captured_at: '2026-10-10T00:00:00Z', event_cursor: 2, omissions: [],
    execution: {
      id: 'execution', conversation_id: 'conversation', target_id: 'target',
      profile: { name: 'test', environment: 'docker', model: '', lifetime_seconds: 900, idle_timeout_seconds: 300 },
      configuration: { name: 'test', model_binding: { model: '' },
        harness_profile: { command: ['agent'], credential_env: [], authentication_method: null, permission_policy: 'deny' },
        environment_template: { environment: 'docker', image: 'test', local_execution_acknowledged: false } },
      created_at: '', last_activity_at: '', state: 'closed', capture_error: null, cleanup_error: null,
      close_reason: 'finished', artifacts: [], artifact_errors: [], source_coverage: 'ACP only',
      turns: [{ id: 'turn', request_id: 'request', status: 'completed', response_text: 'Done',
        capture_complete: true, error: null }],
    },
    events: [
      { execution_id: 'execution', sequence: 1, turn_id: 'turn', timestamp: '', direction: 'incoming',
        payload: { method: 'session/update', params: { update: { sessionUpdate: 'agent_message_chunk',
          content: { type: 'text', text: 'Recorded text' } } } } },
      { execution_id: 'execution', sequence: 2, turn_id: 'turn', timestamp: '', direction: 'lifecycle',
        payload: { type: 'turn_finished', status: 'completed' } },
    ],
    messages: [], scores: [],
    assessment: { playback: { status: 'partial', detail: 'Retained reports only' },
      rescore: operation, recreate: { status: 'unverified', detail: 'Image not verified' }, resume: operation },
  }
}

describe('AgentEvidencePanel', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('does not close or fetch live executions', () => {
    render(<Wrapper><AgentEvidencePanel executionId="execution" closed={false} /></Wrapper>)
    expect(screen.getByRole('button', { name: 'Inspect recording' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Download evidence package' })).toBeDisabled()
    expect(executionsApi.evidence).not.toHaveBeenCalled()
  })

  it('steps retained events without exposing execution controls', async () => {
    const user = userEvent.setup()
    jest.mocked(executionsApi.evidence).mockResolvedValue(recording())
    render(<Wrapper><AgentEvidencePanel executionId="execution" closed /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Inspect recording' }))
    await screen.findByText('Read-only playback · no tools run')
    expect(screen.queryByText('Recorded text')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Next event' }))
    expect(screen.getByText('Recorded text')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Previous event' }))
    expect(screen.queryByText('Recorded text')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Show all events' }))
    expect(screen.getByText('Recorded text')).toBeVisible()
    expect(screen.queryByRole('button', { name: /Allow once|Deny|Continue|Cancel turn/ })).not.toBeInTheDocument()
    expect(screen.getByText('resume: unavailable')).toBeVisible()
    expect(executionsApi.evidence).toHaveBeenCalledTimes(1)
  })

  it('downloads binary data and explicitly reports failures', async () => {
    const user = userEvent.setup()
    const data = new Blob(['archive'], { type: 'application/zip' })
    jest.mocked(executionsApi.downloadEvidence).mockResolvedValueOnce(data).mockRejectedValueOnce(new Error('Evidence too large'))
    render(<Wrapper><AgentEvidencePanel executionId="execution" closed /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Download evidence package' }))
    await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(data, 'pyrit-execution-execution.zip'))
    await user.click(screen.getByRole('button', { name: 'Download evidence package' }))
    expect(await screen.findByText('Evidence too large')).toBeVisible()
    expect(downloadBlob).toHaveBeenCalledTimes(1)
  })

  it('shows the final unknown outcome when capture ended without a terminal event', async () => {
    const user = userEvent.setup()
    const snapshot = recording()
    const turn = snapshot.execution.turns[0]
    jest.mocked(executionsApi.evidence).mockResolvedValue({
      ...snapshot, event_cursor: 1, events: snapshot.events.slice(0, 1),
      execution: { ...snapshot.execution, turns: [{ ...turn, status: 'unknown', response_text: '',
        error: 'Connection lost', capture_complete: false }] },
    })
    render(<Wrapper><AgentEvidencePanel executionId="execution" closed /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Inspect recording' }))
    await screen.findByText('Read-only playback · no tools run')
    await user.click(screen.getByRole('button', { name: 'Show all events' }))
    expect(screen.getByText('unknown', { exact: true })).toBeVisible()
    expect(screen.getByText('Connection lost')).toBeVisible()
    expect(screen.queryByText('running', { exact: true })).not.toBeInTheDocument()
  })
})
