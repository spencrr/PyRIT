import type { ReactNode } from 'react'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AgentTargetConfiguration } from '@/types'

import AgentTargetDialog from './AgentTargetDialog'

jest.mock('@/services/api', () => ({ targetsApi: { createTarget: jest.fn() } }))
jest.mock('@/services/targetRegistry', () => ({ listRegisteredTargets: jest.fn() }))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

describe('AgentTargetDialog', () => {
  it('selects a Responses-only source directly from a fresh form', async () => {
    const user = userEvent.setup()
    const source = makeTarget({ target_registry_name: 'responses-only', target_type: 'OpenAIResponseTarget' })
    source.inference_capabilities = {
      wire_apis: ['responses'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'bound' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: /responses-only/ })
    expect(screen.getByRole('option', { name: /responses-only/ })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Inference protocol'), 'responses')
    await user.selectOptions(screen.getByLabelText('Model source'), 'responses-only')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'responses-only', wire_api: 'responses' },
    })
  })

  it('can change protocols after returning a copied Responses binding to harness-managed access', async () => {
    const user = userEvent.setup()
    const source = makeTarget({ target_registry_name: 'chat-only' })
    source.inference_capabilities = {
      wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    const initial: AgentTargetConfiguration = {
      name: 'copy', model_binding: { model: '', target_registry_name: 'old-source', wire_api: 'responses' },
      harness_profile: { harness_kind: 'copilot', command: ['copilot', '--acp', '--stdio'],
        credential_env: [], authentication_method: null, permission_policy: 'deny' },
      environment_template: { environment: 'docker', image: 'test-image', local_execution_acknowledged: false },
    }
    render(<TestWrapper><AgentTargetDialog initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: /chat-only/ })
    await user.selectOptions(screen.getByLabelText('Model source'), '')
    await user.selectOptions(screen.getByLabelText('Inference protocol'), 'completions')
    await user.selectOptions(screen.getByLabelText('Model source'), 'chat-only')
    expect(screen.getByLabelText('Model source')).toHaveValue('chat-only')
    await user.selectOptions(screen.getByLabelText('Harness'), 'opencode')
    expect(screen.getByRole('option', { name: 'Responses', exact: true })).toBeDisabled()
  })

  it('swaps to OpenCode without prescribing an image or copying a directory', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'open', target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('')
    await user.selectOptions(screen.getByLabelText('Harness'), 'opencode')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'open')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'operator-owned-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { harness_kind: 'opencode', command: ['opencode', 'acp'], credential_env: [] },
      environment_template: { image: 'operator-owned-image', workspace_base: 'image', fixture_directory: null },
    })
  })

  it('accepts an explicit custom ACP command with private local workspace', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'custom', target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.selectOptions(screen.getByLabelText('Harness'), 'acp')
    await user.type(screen.getByLabelText('Executable'), 'test-agent')
    await user.type(screen.getByLabelText('Arguments'), '--stdio{enter}--no-login')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'custom')
    await user.selectOptions(screen.getByLabelText('Execution environment'), 'local')
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { harness_kind: 'acp', command: ['test-agent', '--stdio', '--no-login'] },
      environment_template: { environment: 'local', workspace_base: 'empty', fixture_directory: null },
    })
  })

  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(listRegisteredTargets).mockResolvedValue([])
  })

  it('registers separate configuration primitives and selects the result for chat', async () => {
    const target = makeTarget({ target_type: 'AgentTarget', target_registry_name: 'receipt' })
    jest.mocked(targetsApi.createTarget).mockResolvedValue(target)
    const onCreated = jest.fn()
    const user = userEvent.setup()
    render(<TestWrapper><AgentTargetDialog selectForChat onClose={jest.fn()} onCreated={onCreated} /></TestWrapper>)
    expect(screen.getByRole('region', { name: 'Model binding' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Harness profile' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Environment template' })).toBeInTheDocument()
    expect(screen.getByText(/For tools-off Copilot text or judge calls/)).toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'receipt')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.type(screen.getByLabelText('Harness-managed model ID'), 'test-model')
    await user.click(screen.getByRole('button', { name: 'Register and select for chat' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(target))
    expect(targetsApi.createTarget).toHaveBeenCalledWith({
      name: 'receipt', type: 'AgentTarget', params: { agent_configuration: expect.objectContaining({
        model_binding: { model: 'test-model' },
        harness_profile: expect.objectContaining({ permission_policy: 'ask' }),
        environment_template: expect.objectContaining({ environment: 'docker' }),
      }) },
    })
  })

  it('requires explicit local acknowledgement and preserves a copied harness without authentication', async () => {
    const initial: AgentTargetConfiguration = {
      name: 'custom', model_binding: { model: 'same' },
      harness_profile: { command: ['test-agent', '--stdio'], credential_env: [], authentication_method: null, permission_policy: 'deny' },
      environment_template: { environment: 'local', image: null, local_execution_acknowledged: true },
    }
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetDialog initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { command: ['test-agent', '--stdio'], authentication_method: null },
    })
    expect(initial.name).toBe('custom')
  })

  it('surfaces registration errors without dismissing the form', async () => {
    jest.mocked(targetsApi.createTarget).mockRejectedValue(new Error('Registration rejected'))
    const user = userEvent.setup()
    const onCreated = jest.fn()
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={onCreated} /></TestWrapper>)
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'receipt')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    expect(await screen.findByText('Registration rejected')).toBeInTheDocument()
    expect(onCreated).not.toHaveBeenCalled()
  })

  it('selects an existing target by inference capabilities and never copies its credentials', async () => {
    const source = makeTarget({ target_registry_name: 'source', target_type: 'CustomInferenceTarget' })
    source.inference_capabilities = {
      wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source, makeTarget({ target_registry_name: 'unsupported' })])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_type: 'AgentTarget' }))
    const user = userEvent.setup()
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'source — inference compatible' })
    expect(screen.getByRole('option', { name: /unsupported — No inference implementation/ })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Model source'), 'source')
    await user.selectOptions(screen.getByLabelText('Harness'), 'opencode')
    expect(screen.getByLabelText('Model source')).toHaveValue('source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound-agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'source', target_identifier_hash: source.identifier.hash, wire_api: 'completions' },
      harness_profile: { harness_kind: 'opencode', credential_env: [], authentication_method: null },
    })
  })
})
