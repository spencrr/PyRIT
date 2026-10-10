import type { ReactNode } from 'react'

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { useRuntime } from '@/hooks/useRuntime'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AgentPresetCatalog, AgentTargetConfiguration, TargetInstance } from '@/types'

import AgentTargetDialog from './AgentTargetDialog'

jest.mock('@/services/api', () => ({ targetsApi: { createTarget: jest.fn(), listAgentPresets: jest.fn() } }))
jest.mock('@/services/targetRegistry', () => ({ listRegisteredTargets: jest.fn() }))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

function presetCatalog(): AgentPresetCatalog {
  return {
    harness_profiles: [{
      name: 'team_harness', initializer: 'PrivateHarnessInitializer',
      profile: { harness_kind: 'opencode', command: ['team-opencode', 'acp', '--custom'],
        credential_env: ['TEST_TOOL_TOKEN'], authentication_method: null, permission_policy: 'deny',
        inference_requirements: { wire_api: 'completions', streaming: true, tool_calls: true, input_modalities: ['text', 'image'] },
      },
    }],
    environment_templates: [{
      name: 'team_workspace', initializer: 'PrivateEnvironmentInitializer',
      template: { environment: 'docker', image: 'team:1', local_execution_acknowledged: false,
        workspace_base: 'image', working_directory: 'project', fixture_directory: 'fixture',
        expected_fixture_sha256: 'a'.repeat(64), docker_network: 'none', docker_memory: '2g', docker_cpus: 3 },
    }],
    unavailable_targets: {},
  }
}

function modelSource(hash: string): TargetInstance {
  const target = makeTarget({ target_registry_name: 'model_source', identifier_hash: hash })
  target.inference_capabilities = {
    wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
  }
  return target
}

describe('AgentTargetDialog', () => {
  it.each(['reload', 'generation'])('requires explicit acceptance when %s replaces a pinned model source', async (trigger: string) => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    const view = render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source — inference compatible' })
    await user.selectOptions(screen.getByLabelText('Model source'), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-b')])
    if (trigger === 'reload') {
      await user.click(screen.getByRole('button', { name: 'Reload presets' }))
    } else {
      jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'replacement' })
      view.rerender(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    }
    const accept = await screen.findByRole('button', { name: 'Use updated model source' })
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    await user.click(accept)
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'model_source', target_identifier_hash: 'hash-b' },
    })
  })

  it.each(['pending', 'removed', 'failed'])('blocks a pinned source while its refresh is %s', async (outcome: string) => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source — inference compatible' })
    await user.selectOptions(screen.getByLabelText('Model source'), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    if (outcome === 'pending') jest.mocked(listRegisteredTargets).mockReturnValueOnce(new Promise(() => {}))
    else if (outcome === 'removed') jest.mocked(listRegisteredTargets).mockResolvedValueOnce([])
    else jest.mocked(listRegisteredTargets).mockRejectedValueOnce(new Error('model catalog unavailable'))
    await user.click(screen.getByRole('button', { name: 'Reload presets' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload presets' })).toBeEnabled())
    expect(screen.getByLabelText('Model source')).toHaveValue('model_source')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Use updated model source' })).not.toBeInTheDocument()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
  })

  it('keeps the original model pin when a refresh returns the same identity', async () => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source — inference compatible' })
    await user.selectOptions(screen.getByLabelText('Model source'), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Reload presets' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: 'Use updated model source' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_identifier_hash: 'hash-a' },
    })
  })

  it.each(['hash-a', null])('does not silently rebind a copied target with pin %s', async (pin: string | null) => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-b')])
    const initial: AgentTargetConfiguration = {
      name: 'copy', model_binding: { model: '', target_registry_name: 'model_source', target_identifier_hash: pin },
      harness_profile: { harness_kind: 'copilot', command: ['copilot', '--acp', '--stdio'],
        permission_policy: 'deny', credential_env: [], authentication_method: null },
      environment_template: { environment: 'docker', image: 'test-image', local_execution_acknowledged: false },
    }
    render(<TestWrapper><AgentTargetDialog initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('button', { name: 'Use updated model source' })
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    await user.selectOptions(screen.getByLabelText('Model source'), '')
    await user.selectOptions(screen.getByLabelText('Model source'), 'model_source')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
  })

  it('keeps a copied target snapshot independent of newly registered presets', async () => {
    const user = userEvent.setup()
    const catalog = presetCatalog()
    const initial: AgentTargetConfiguration = {
      name: 'existing', model_binding: { model: '' },
      harness_profile: catalog.harness_profiles[0].profile,
      environment_template: { ...catalog.environment_templates[0].template, image: 'pinned-old-image' },
    }
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'copy' }))
    render(<TestWrapper><AgentTargetDialog initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_workspace' })
    expect(screen.getByLabelText('Registered environment template')).toHaveValue('')
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('pinned-old-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    const sent = jest.mocked(targetsApi.createTarget).mock.calls[0][0]
    expect(sent.agent_presets).toBeUndefined()
    expect(sent.params.agent_configuration).toMatchObject({
      harness_profile: initial.harness_profile,
      environment_template: initial.environment_template,
    })
  })

  it('uses selected profile modalities to check model binding compatibility', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(presetCatalog())
    const model = makeTarget({ target_registry_name: 'text_only' })
    model.inference_capabilities = {
      wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([model])
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Model source'), 'text_only')
    await user.selectOptions(screen.getByLabelText('Registered harness profile'), 'team_harness')
    expect(screen.getByRole('option', { name: /text_only — Missing required input modality/ })).toBeDisabled()
    expect(screen.getByText('Selected model source is unavailable or incompatible.')).toBeVisible()
  })

  it('accepts a changed named template only after an explicit update action', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(presetCatalog())
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_workspace' })
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'team_workspace')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'test')
    const next = presetCatalog()
    next.environment_templates[0] = { ...next.environment_templates[0],
      template: { ...next.environment_templates[0].template, image: 'team:2' } }
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(next)
    await user.click(screen.getByRole('button', { name: 'Reload presets' }))
    const update = await screen.findByRole('button', { name: 'Use updated template' })
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('team:1')
    await user.click(update)
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('team:2')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
  })

  it('registers complete preset snapshots with provenance without modifying their definitions', async () => {
    const user = userEvent.setup()
    const catalog = presetCatalog()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Registered harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'team_workspace')
    expect(screen.getByLabelText('Harness')).toBeDisabled()
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toBeDisabled()
    expect(screen.getByText(/Registered by PrivateHarnessInitializer/)).toBeVisible()
    expect(screen.getByText(/Network: none/)).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    const sent = jest.mocked(targetsApi.createTarget).mock.calls[0][0]
    expect(sent.agent_presets).toEqual({ harness_profile: 'team_harness', environment_template: 'team_workspace' })
    expect(sent.params.agent_configuration).toMatchObject({
      harness_profile: catalog.harness_profiles[0].profile,
      environment_template: catalog.environment_templates[0].template,
    })
    expect(catalog).toEqual(presetCatalog())
  })

  it('customizes copied preset settings without carrying false preset provenance', async () => {
    const user = userEvent.setup()
    const catalog = presetCatalog()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'custom' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Registered harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'team_workspace')
    await user.click(screen.getByRole('button', { name: 'Customize profile' }))
    await user.click(screen.getByRole('button', { name: 'Customize template' }))
    await user.selectOptions(screen.getByLabelText('Tool permission policy'), 'ask')
    await user.clear(screen.getByRole('textbox', { name: /Docker image/ }))
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:2')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'custom')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    const sent = jest.mocked(targetsApi.createTarget).mock.calls[0][0]
    expect(sent.agent_presets).toBeUndefined()
    expect(sent.params.agent_configuration).toMatchObject({
      harness_profile: { ...catalog.harness_profiles[0].profile, permission_policy: 'ask' },
      environment_template: { ...catalog.environment_templates[0].template, image: 'team:2' },
    })
    expect(catalog).toEqual(presetCatalog())
  })

  it('requires explicit local consent after selecting each environment template', async () => {
    const user = userEvent.setup()
    const catalog = presetCatalog()
    catalog.environment_templates[0] = {
      name: 'local', initializer: 'EnvironmentTemplateInitializer',
      template: { environment: 'local', image: null, local_execution_acknowledged: false },
    }
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'local' })
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'local')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'local')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('Registered environment template'), '')
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'local')
    expect(screen.getByLabelText('I understand local tools can access the host')).not.toBeChecked()
  })

  it.each(['changed', 'removed', 'failed'])('blocks stale selected presets after a %s reload', async (outcome: string) => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(presetCatalog())
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Registered harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Registered environment template'), 'team_workspace')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'test')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
    const next = presetCatalog()
    if (outcome === 'removed') next.harness_profiles.splice(0)
    if (outcome === 'changed') next.harness_profiles[0] = { ...next.harness_profiles[0],
      profile: { ...next.harness_profiles[0].profile, command: ['different', 'acp'] } }
    if (outcome === 'failed') jest.mocked(targetsApi.listAgentPresets).mockRejectedValueOnce(new Error('catalog offline'))
    else jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(next)
    await user.click(screen.getByRole('button', { name: 'Reload presets' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Reload presets' })).toBeEnabled())
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    expect(screen.getByText(/selected preset is unavailable or changed/)).toBeVisible()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Customize profile' }))
    if (outcome === 'failed') await user.click(screen.getByRole('button', { name: 'Customize template' }))
    expect(screen.getByRole('button', { name: 'Register target' })).toBeEnabled()
  })

  it('reports unavailable initializer compositions without claiming they are targets', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      ...presetCatalog(), unavailable_targets: { optional_agent: 'Model target missing' },
    })
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.click(await screen.findByText('Unavailable initializer targets (1)'))
    expect(screen.getByText('optional_agent: Model target missing')).toBeVisible()
    expect(screen.getByText(/Registration here is session-only/)).toBeVisible()
  })

  it('preserves explicitly selected tool credentials when switching to BYOK', async () => {
    const user = userEvent.setup()
    const source = makeTarget({ target_registry_name: 'source' })
    source.inference_capabilities = {
      wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'bound' }))
    render(<TestWrapper><AgentTargetDialog onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByText('Launch and authentication'))
    await user.type(screen.getByLabelText('Harness credential references'), 'TEST_REPO_CREDENTIAL')
    await user.type(screen.getByLabelText('ACP authentication method'), 'copilot-login')
    await screen.findByRole('option', { name: 'source — inference compatible' })
    await user.selectOptions(screen.getByLabelText('Model source'), 'source')
    expect(screen.getByLabelText('Harness credential references')).toHaveValue('TEST_REPO_CREDENTIAL')
    expect(screen.getByLabelText('ACP authentication method')).toHaveValue('copilot-login')
    expect(screen.getByRole('button', { name: 'Register target' })).toBeDisabled()
    expect(screen.getByText(/Remove the ACP authentication method explicitly/)).toBeVisible()
    await user.clear(screen.getByLabelText('ACP authentication method'))
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Register target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'source' },
      harness_profile: { credential_env: ['TEST_REPO_CREDENTIAL'], authentication_method: null },
    })
  })

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
    jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'initial' })
    jest.mocked(targetsApi.listAgentPresets).mockReset()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      harness_profiles: [], environment_templates: [], unavailable_targets: {},
    })
    jest.mocked(listRegisteredTargets).mockReset()
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
