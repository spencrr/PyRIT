import type { ReactNode } from 'react'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { executionsApi } from '@/services/executions'
import type { AgentCheckpointStatus, AgentExecution } from '@/types'

import AgentCheckpointPanel from './AgentCheckpointPanel'

jest.mock('@/services/executions', () => ({ executionsApi: {
  checkpoint: jest.fn(), saveCheckpoint: jest.fn(), restoreCheckpoint: jest.fn(), deleteCheckpoint: jest.fn(),
} }))

function Wrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

const saved: AgentCheckpointStatus = {
  save_available: false, restore_available: true, detail: 'Sensitive state saved',
  checkpoint: {
    id: 'checkpoint', source_execution_id: 'old', created_at: '2026-10-10T00:00:00Z',
    conversation_id: 'chat', target_id: 'target', image_id: 'image', session_id: 'session', agent_version: '1',
    event_cursor: 10, workspace: { bytes: 1, sha256: 'a' }, harness_state: { bytes: 2, sha256: 'b' },
  },
}

describe('AgentCheckpointPanel', () => {
  beforeEach(() => jest.clearAllMocks())

  it('requires explicit state-retention consent and never saves during inspection', async () => {
    const user = userEvent.setup()
    const changed = jest.fn()
    if (saved.checkpoint) jest.mocked(executionsApi.saveCheckpoint).mockResolvedValue(saved.checkpoint)
    jest.mocked(executionsApi.checkpoint).mockResolvedValueOnce({
      save_available: true, restore_available: false, checkpoint: null, detail: 'Ready',
    }).mockResolvedValueOnce(saved)
    render(<Wrapper><AgentCheckpointPanel executionId="old" onChanged={changed} /></Wrapper>)
    expect(executionsApi.checkpoint).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Checkpoint options' }))
    expect(await screen.findByRole('button', { name: 'Save and close' })).toBeDisabled()
    expect(executionsApi.saveCheckpoint).not.toHaveBeenCalled()
    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: 'Save and close' }))
    await screen.findByText(/Checkpoint saved/)
    expect(executionsApi.saveCheckpoint).toHaveBeenCalledWith('old')
    expect(changed).toHaveBeenCalledTimes(1)
  })

  it('restores only on explicit request and hands the new allocation to its parent', async () => {
    const user = userEvent.setup()
    const changed = jest.fn()
    const restored: AgentExecution = {
      id: 'new', parent_execution_id: 'old', conversation_id: 'chat', target_id: 'target', state: 'idle',
      profile: { name: 'test', environment: 'docker', model: '', idle_timeout_seconds: 300, lifetime_seconds: 900 },
      configuration: { name: 'test', model_binding: { model: '' },
        harness_profile: { command: ['agent'], credential_env: [], authentication_method: null, permission_policy: 'deny' },
        environment_template: { environment: 'docker', image: 'test', local_execution_acknowledged: false } },
      created_at: '', last_activity_at: '', turns: [], capture_error: null, cleanup_error: null,
      close_reason: null, artifacts: [], artifact_errors: [], source_coverage: 'ACP',
    }
    jest.mocked(executionsApi.checkpoint).mockResolvedValue(saved)
    jest.mocked(executionsApi.restoreCheckpoint).mockResolvedValue(restored)
    render(<Wrapper><AgentCheckpointPanel executionId="old" onChanged={changed} /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Checkpoint options' }))
    await user.click(await screen.findByRole('button', { name: 'Restore same chat' }))
    await waitFor(() => expect(changed).toHaveBeenCalledWith(restored))
    expect(executionsApi.saveCheckpoint).not.toHaveBeenCalled()
  })

  it('requires confirmation to delete native state without calling execution close', async () => {
    const user = userEvent.setup()
    jest.mocked(executionsApi.checkpoint).mockResolvedValue(saved)
    jest.mocked(executionsApi.deleteCheckpoint).mockResolvedValue({
      save_available: false, restore_available: false, checkpoint: null, detail: 'Deleted',
    })
    render(<Wrapper><AgentCheckpointPanel executionId="old" onChanged={jest.fn()} /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Checkpoint options' }))
    await user.click(await screen.findByRole('button', { name: 'Delete checkpoint' }))
    expect(executionsApi.deleteCheckpoint).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Confirm checkpoint deletion' }))
    await screen.findByText(/Private checkpoint state deleted/)
    expect(screen.queryByRole('button', { name: 'Restore same chat' })).not.toBeInTheDocument()
  })

  it('disables rewind and shows restore failures without a success notification', async () => {
    const user = userEvent.setup()
    jest.mocked(executionsApi.checkpoint).mockResolvedValueOnce({ ...saved, restore_available: false }).mockResolvedValueOnce(saved)
    jest.mocked(executionsApi.restoreCheckpoint).mockRejectedValue(new Error('Conversation changed'))
    render(<Wrapper><AgentCheckpointPanel executionId="old" onChanged={jest.fn()} /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Checkpoint options' }))
    expect(await screen.findByRole('button', { name: 'Restore same chat' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Refresh checkpoint status' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Restore same chat' })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Restore same chat' }))
    await screen.findByText('Conversation changed')
    expect(screen.queryByText(/Restored into a new allocation/)).not.toBeInTheDocument()
  })

  it('keeps incomplete deletion retryable even when its manifest is already gone', async () => {
    const user = userEvent.setup()
    jest.mocked(executionsApi.checkpoint).mockResolvedValue({
      checkpoint: null, save_available: false, restore_available: false, deletion_pending: true, detail: 'Deletion incomplete',
    })
    render(<Wrapper><AgentCheckpointPanel executionId="old" onChanged={jest.fn()} /></Wrapper>)
    await user.click(screen.getByRole('button', { name: 'Checkpoint options' }))
    expect(await screen.findByRole('button', { name: 'Retry checkpoint deletion' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: 'Restore same chat' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Retry checkpoint deletion' }))
    expect(screen.getByRole('button', { name: 'Confirm checkpoint deletion' })).toBeVisible()
  })
})
