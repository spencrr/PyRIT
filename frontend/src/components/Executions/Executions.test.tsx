import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { MemoryRouter } from 'react-router'

import { executionsApi } from '@/services/executions'
import type { AgentExecution } from '@/types'

import Executions from './Executions'

jest.mock('@/services/executions', () => ({
  executionsApi: { list: jest.fn(), get: jest.fn(), events: jest.fn(), cancel: jest.fn(), close: jest.fn() },
}))
jest.mock('@/services/api', () => ({ targetsApi: { createTarget: jest.fn() } }))

const record: AgentExecution = {
  id: '12345678-0000-0000-0000-000000000001', conversation_id: 'conversation', target_id: 'target',
  profile: { name: 'receipt', environment: 'docker', model: '', idle_timeout_seconds: 300, lifetime_seconds: 900 },
  configuration: {
    name: 'receipt', model_binding: { model: '' },
    harness_profile: { command: ['copilot', '--acp'], credential_env: [], authentication_method: null, permission_policy: 'deny' },
    environment_template: { environment: 'docker', image: 'test', local_execution_acknowledged: false },
  },
  state: 'working', created_at: '2026-10-07T00:00:00Z', last_activity_at: '2026-10-07T00:00:00Z',
  turns: [{ id: 'turn', status: 'running', stop_reason: null, capture_complete: false, error: null }],
  capture_error: null, cleanup_error: null, close_reason: null,
  artifacts: [], artifact_errors: [], source_coverage: 'ACP-exposed events only',
}

function renderPage(canManage = true): void {
  render(<MemoryRouter><FluentProvider theme={webLightTheme}>
    <Executions canManage={canManage} onTargetCreated={jest.fn()} />
  </FluentProvider></MemoryRouter>)
}

describe('Executions', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(executionsApi.list).mockResolvedValue([record])
    jest.mocked(executionsApi.get).mockResolvedValue(record)
    jest.mocked(executionsApi.events).mockResolvedValue({ events: [], next_cursor: 0 })
    jest.mocked(executionsApi.cancel).mockResolvedValue({ ...record, state: 'idle' })
  })

  it('does not fetch administration data without permission', () => {
    renderPage(false)
    expect(screen.getByText(/Administrator access is required/)).toBeInTheDocument()
    expect(executionsApi.list).not.toHaveBeenCalled()
  })

  it('shows live resource state and cancels the turn separately from closing', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(await screen.findByRole('button', { name: '12345678' }))
    expect(await screen.findByText('ACP-exposed events only')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Cancel turn' }))
    await waitFor(() => expect(executionsApi.cancel).toHaveBeenCalledWith(record.id))
    expect(executionsApi.close).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: 'Copy configuration to new target' })).toBeInTheDocument()
  })
})
