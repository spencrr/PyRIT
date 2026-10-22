import type { ReactNode } from 'react'

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { FluentProvider, webLightTheme } from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { useRuntime } from '@/hooks/useRuntime'
import { makeTarget } from '@/test-utils/targetFixtures'
import type { AgentPresetCatalog, AgentTargetConfiguration, HarnessAdapterInfo, HarnessProfile, TargetInstance } from '@/types'

import AgentTargetForm from './AgentTargetForm'
import { BUILTIN_ENVIRONMENT_PROVIDERS } from './environmentProviders'

jest.mock('@/services/api', () => ({ targetsApi: {
  createTarget: jest.fn(), listAgentPresets: jest.fn(), getStartupDestination: jest.fn(), saveForStartup: jest.fn(),
} }))
jest.mock('@/services/targetRegistry', () => ({ listRegisteredTargets: jest.fn() }))
jest.mock('@/hooks/useRuntime', () => ({ useRuntime: jest.fn() }))

function TestWrapper({ children }: { children: ReactNode }) {
  return <FluentProvider theme={webLightTheme}>{children}</FluentProvider>
}

// A registered third-party adapter alongside the built-ins; OpenCode is not shipped by PyRIT.
const CATALOG_ADAPTERS: HarnessAdapterInfo[] = [
  { name: 'copilot', display_name: 'GitHub Copilot', default_command: ['copilot', '--acp', '--stdio'], wire_apis: ['completions', 'responses'] },
  { name: 'opencode', display_name: 'OpenCode', default_command: ['opencode', 'acp'], wire_apis: ['completions'] },
  { name: 'acp', display_name: 'Custom ACP', default_command: [], wire_apis: [] },
]

function presetCatalog(): AgentPresetCatalog {
  return {
    harness_adapters: CATALOG_ADAPTERS,
    harness_profiles: [{
      name: 'team_harness', initializer: 'PrivateHarnessInitializer',
      profile: { harness_kind: 'copilot', command: ['team-copilot', '--acp', '--stdio', '--custom'],
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

function environmentCatalog(): AgentPresetCatalog {
  const working = { name: 'working_directory', type_name: 'str', required: false, default: '.', description: '' }
  return {
    ...presetCatalog(),
    environment_parameters: {
      docker: [working,
        { name: 'docker_network', type_name: 'str', required: false, default: 'bridge', description: '' },
        { name: 'docker_memory', type_name: 'str', required: false, default: '1g', description: '' },
        { name: 'docker_cpus', type_name: 'float', required: false, default: '1', description: '' },
      ],
      local: [working],
    },
  }
}

describe('AgentTargetForm', () => {
  it('explains native Copilot authentication until a token reference or model source is chosen', async () => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash')])
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(await screen.findByText(/A model ID does not authenticate Copilot/)).toBeVisible()
    expect(screen.queryByRole('checkbox', { name: /GitHub CLI login/ })).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /Harness credential references/ }), 'COPILOT_GITHUB_TOKEN')
    expect(screen.queryByText(/A model ID does not authenticate Copilot/)).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'native')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'copilot:test')
    await user.click(screen.getByRole('button', { name: 'Create Target', exact: true }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalledTimes(1))
    const params = jest.mocked(targetsApi.createTarget).mock.calls[0][0].params as {
      agent_configuration: { harness_profile: Record<string, unknown> }
    }
    expect(params.agent_configuration.harness_profile.credential_env).toEqual(['COPILOT_GITHUB_TOKEN'])
    expect(params.agent_configuration.harness_profile).not.toHaveProperty('use_github_cli_login')
  })

  it('selects installed harness adapters and preserves their explicit command and supported API', async () => {
    const user = userEvent.setup()
    const source = modelSource('source-hash')
    source.inference_capabilities = {
      wire_apis: ['responses', 'completions'], streaming: true, tool_calls: true,
      input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      ...presetCatalog(),
      harness_adapters: [{
        name: 'team_v1', display_name: 'Team agent', default_command: ['team-agent', '--stdio'], wire_apis: ['responses'],
      }],
    })
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'Team agent' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'team_v1')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'team_agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:1')
    await user.click(screen.getByRole('button', { name: 'Create Target', exact: true }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalledTimes(1))
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params).toMatchObject({
      agent_configuration: {
        harness_profile: { harness_kind: 'team_v1', command: ['team-agent', '--stdio'] },
        model_binding: { target_registry_name: 'model_source', target_identifier_hash: 'source-hash', wire_api: 'responses' },
      },
    })
  })

  it('does not silently reinterpret a copied custom adapter when it is unavailable', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({ ...presetCatalog(), harness_adapters: [] })
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} initialConfiguration={{
      name: 'copied', model_binding: { model: '' },
      harness_profile: { harness_kind: 'team_v1', command: ['team-agent'], permission_policy: 'deny',
        credential_env: [], authentication_method: null },
      environment_template: { environment: 'docker', image: 'test', local_execution_acknowledged: false },
    }} /></TestWrapper>)
    expect(await screen.findByText(/Harness adapter is unavailable/)).toBeVisible()
    expect(screen.getByRole('combobox', { name: 'Harness', exact: true })).toHaveValue('team_v1')
    await user.click(screen.getByRole('button', { name: 'Create Target', exact: true }))
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
  })

  it('saves a startup definition without pretending to register or select a live target', async () => {
    const user = userEvent.setup()
    const onCreated = jest.fn()
    jest.mocked(targetsApi.getStartupDestination).mockResolvedValue({ source: 'startup.yaml', version: 'v1' })
    jest.mocked(targetsApi.saveForStartup).mockResolvedValue({
      name: 'saved_agent', source: 'startup.yaml', version: 'v2', target_identifier_hash: 'hash', model_target: null, applied: false,
    })
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={onCreated} /></TestWrapper>)
    expect(targetsApi.getStartupDestination).not.toHaveBeenCalled()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'saved_agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:1')
    await user.click(screen.getByLabelText('Save for startup instead of creating a session target'))
    expect(await screen.findByText('startup.yaml')).toBeVisible()
    await user.click(screen.getByRole('button', { name: 'Save for startup', exact: true }))
    await screen.findByText(/Saved saved_agent for the next backend start/)
    expect(screen.getByRole('button', { name: 'Close', exact: true })).toBeEnabled()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    expect(onCreated).not.toHaveBeenCalled()
    expect(jest.mocked(targetsApi.saveForStartup).mock.calls[0][0]).toMatchObject({
      name: 'saved_agent', version: 'v1',
      configuration: { name: 'saved_agent', environment_template: { image: 'team:1' } },
    })
    await user.click(screen.getByRole('button', { name: 'Save for startup', exact: true }))
    await waitFor(() => expect(targetsApi.saveForStartup).toHaveBeenCalledTimes(2))
    expect(jest.mocked(targetsApi.saveForStartup).mock.calls[1][0].version).toBe('v2')
  })

  it('keeps a conflicted draft and reloads its destination only on explicit request', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.getStartupDestination).mockResolvedValue({ source: 'startup.yaml', version: 'v1' })
    jest.mocked(targetsApi.saveForStartup).mockRejectedValueOnce(new Error('Configuration changed; reload the destination'))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'saved_agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:1')
    await user.click(screen.getByLabelText('Save for startup instead of creating a session target'))
    await screen.findByText('startup.yaml')
    await user.click(screen.getByRole('button', { name: 'Save for startup', exact: true }))
    expect(await screen.findByText('Configuration changed; reload the destination')).toBeVisible()
    expect(screen.getByRole('textbox', { name: /Target name/ })).toHaveValue('saved_agent')
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('team:1')
    expect(targetsApi.getStartupDestination).toHaveBeenCalledTimes(1)
    jest.mocked(targetsApi.getStartupDestination).mockResolvedValueOnce({ source: 'startup.yaml', version: 'v3' })
    jest.mocked(targetsApi.saveForStartup).mockResolvedValueOnce({
      name: 'saved_agent', source: 'startup.yaml', version: 'v4', target_identifier_hash: 'hash', model_target: null, applied: false,
    })
    await user.click(screen.getByRole('button', { name: 'Reload destination' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Save for startup', exact: true })).toBeEnabled())
    await user.click(screen.getByRole('button', { name: 'Save for startup', exact: true }))
    await waitFor(() => expect(targetsApi.saveForStartup).toHaveBeenCalledTimes(2))
    expect(jest.mocked(targetsApi.saveForStartup).mock.calls[1][0].version).toBe('v3')
  })

  it('does not silently fall back to session creation when startup storage is unavailable', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.getStartupDestination).mockRejectedValueOnce(new Error('storage unavailable'))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'saved_agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:1')
    await user.click(screen.getByLabelText('Save for startup instead of creating a session target'))
    expect(await screen.findByText('storage unavailable')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save for startup', exact: true })).toBeDisabled()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    await user.click(screen.getByLabelText('Save for startup instead of creating a session target'))
    expect(screen.getByRole('button', { name: 'Create Target', exact: true })).toBeEnabled()
  })

  it.each(['pending', 'failed', 'missing', 'empty', 'removed-field'])('retains environment edits until %s metadata is available after reinitialization', async (outcome: string) => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(environmentCatalog())
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'custom' }))
    const view = render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByText('Environment settings'))
    await screen.findByLabelText('Network')
    await user.clear(screen.getByLabelText('Network'))
    await user.type(screen.getByLabelText('Network'), 'none')
    await user.clear(screen.getByLabelText('Working directory'))
    await user.type(screen.getByLabelText('Working directory'), 'project')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'custom')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'image')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
    let resolveCatalog: ((catalog: AgentPresetCatalog) => void) | undefined
    if (outcome === 'pending') {
      jest.mocked(targetsApi.listAgentPresets).mockReturnValueOnce(new Promise((resolve) => { resolveCatalog = resolve }))
    } else if (outcome === 'failed') {
      jest.mocked(targetsApi.listAgentPresets).mockRejectedValueOnce(new Error('metadata offline'))
    } else if (outcome === 'empty' || outcome === 'removed-field') {
      const next = environmentCatalog()
      next.environment_parameters = {
        ...next.environment_parameters,
        docker: outcome === 'empty' ? [] : next.environment_parameters?.docker?.filter((parameter) => parameter.name !== 'docker_network'),
      }
      jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(next)
    } else {
      jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(presetCatalog())
    }
    jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'replacement' })
    view.rerender(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await waitFor(() => expect(targetsApi.listAgentPresets).toHaveBeenCalledTimes(2))
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    const form = screen.getByRole('button', { name: 'Create Target' }).closest('form')
    if (!form) throw new Error('Expected the agent form')
    fireEvent.submit(form)
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    if (outcome === 'pending') {
      await act(async () => { resolveCatalog?.(environmentCatalog()) })
    } else {
      jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(environmentCatalog())
      await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    }
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled())
    expect(screen.getByLabelText('Network')).toHaveValue('none')
    expect(screen.getByLabelText('Working directory')).toHaveValue('project')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      environment_template: { docker_network: 'none', working_directory: 'project' },
    })
  })

  it('keeps Model first, labels models by connection, and infers a sole compatible protocol', async () => {
    const user = userEvent.setup()
    const source = modelSource('hash')
    source.identifier.model_name = 'deployment_a'
    source.identifier.underlying_model_name = 'gpt-4.1'
    source.inference_capabilities = {
      wire_apis: ['responses'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'test' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(screen.getAllByRole('region').map((region) => region.getAttribute('aria-label'))).toEqual(['Model', 'Harness', 'Environment'])
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }),
      await screen.findByRole('option', { name: 'gpt-4.1 · model_source' }))
    expect(screen.queryByLabelText('Inference protocol')).not.toBeInTheDocument()
    await user.click(screen.getByText('Connection details'))
    expect(screen.getByText('Protocol: Responses')).toBeVisible()
    expect(screen.getByText('Deployment: deployment_a')).toBeVisible()
    await user.type(screen.getByLabelText('Harness model hint'), 'gpt-4.1')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'test')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { model: 'gpt-4.1', target_registry_name: 'model_source', target_identifier_hash: 'hash', wire_api: 'responses' },
    })
  })

  it('offers protocol choice only for multiple compatible connections and adapts an explicit harness switch', async () => {
    const user = userEvent.setup()
    const source = modelSource('hash')
    source.inference_capabilities = {
      wire_apis: ['responses', 'completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    await user.selectOptions(screen.getByLabelText('Inference protocol'), 'responses')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'opencode')
    expect(screen.queryByLabelText('Inference protocol')).not.toBeInTheDocument()
    expect(screen.getByText('Protocol: Chat Completions')).toBeInTheDocument()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'acp')
    expect(screen.getByText('Selected model source is unavailable or incompatible.')).toBeVisible()
  })

  it('renders environment parameters from metadata and preserves hidden copied settings', async () => {
    const user = userEvent.setup()
    const catalog = environmentCatalog()
    const initial: AgentTargetConfiguration = {
      name: 'copy', model_binding: { model: '' },
      harness_profile: catalog.harness_profiles[0].profile,
      environment_template: catalog.environment_templates[0].template,
      artifact_paths: ['space in file.json', 'a,b.json'],
    }
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'test' }))
    render(<TestWrapper><AgentTargetForm initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_workspace' })
    await user.click(screen.getByText('Environment settings'))
    expect(screen.getByLabelText('Network')).toHaveValue('none')
    expect(screen.getByLabelText('Memory limit')).toHaveValue('2g')
    await user.clear(screen.getByLabelText('CPU cores'))
    await user.type(screen.getByLabelText('CPU cores'), '4')
    expect(screen.queryByRole('textbox', { name: /Import directory|Artifacts to retain/ })).not.toBeInTheDocument()
    await user.selectOptions(screen.getByLabelText('Execution environment'), 'local')
    expect(screen.queryByLabelText('Network')).not.toBeInTheDocument()
    expect(screen.getByLabelText('Working directory')).toHaveValue('project')
    await user.selectOptions(screen.getByLabelText('Execution environment'), 'docker')
    expect(screen.getByLabelText('CPU cores')).toHaveValue(4)
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      artifact_paths: initial.artifact_paths,
      environment_template: { ...initial.environment_template, docker_cpus: 4 },
    })
  })

  it.each(['reload', 'generation'])('requires explicit acceptance when %s replaces a pinned model source', async (trigger: string) => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    const view = render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-b')])
    if (trigger === 'reload') {
      await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    } else {
      jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'replacement' })
      view.rerender(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    }
    const accept = await screen.findByRole('button', { name: 'Use updated model source' })
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    await user.click(accept)
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'model_source', target_identifier_hash: 'hash-b' },
    })
  })

  it.each(['pending', 'removed', 'failed'])('blocks a pinned source while its refresh is %s', async (outcome: string) => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    if (outcome === 'pending') jest.mocked(listRegisteredTargets).mockReturnValueOnce(new Promise(() => {}))
    else if (outcome === 'removed') jest.mocked(listRegisteredTargets).mockResolvedValueOnce([])
    else jest.mocked(listRegisteredTargets).mockRejectedValueOnce(new Error('model catalog unavailable'))
    await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh options' })).toBeEnabled())
    expect(screen.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('model_source')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: 'Use updated model source' })).not.toBeInTheDocument()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
  })

  it('keeps the original model pin when a refresh returns the same identity', async () => {
    const user = userEvent.setup()
    jest.mocked(listRegisteredTargets).mockResolvedValue([modelSource('hash-a')])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'model_source' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled())
    expect(screen.queryByRole('button', { name: 'Use updated model source' })).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('button', { name: 'Use updated model source' })
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), '')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'model_source')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
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
    render(<TestWrapper><AgentTargetForm initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_workspace' })
    expect(screen.getByLabelText('Environment template')).toHaveValue('')
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('pinned-old-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'text_only')
    await user.selectOptions(screen.getByLabelText('Harness profile'), 'team_harness')
    expect(screen.getByRole('option', { name: /text_only — Missing required input modality/ })).toBeDisabled()
    expect(screen.getByText('Selected model source is unavailable or incompatible.')).toBeVisible()
  })

  it('accepts a changed named template only after an explicit update action', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(presetCatalog())
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_workspace' })
    await user.selectOptions(screen.getByLabelText('Environment template'), 'team_workspace')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'test')
    const next = presetCatalog()
    next.environment_templates[0] = { ...next.environment_templates[0],
      template: { ...next.environment_templates[0].template, image: 'team:2' } }
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(next)
    await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    const update = await screen.findByRole('button', { name: 'Use updated template' })
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('team:1')
    await user.click(update)
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('team:2')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
  })

  it('registers complete preset snapshots with provenance without modifying their definitions', async () => {
    const user = userEvent.setup()
    const catalog = presetCatalog()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(catalog)
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'composed' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Environment template'), 'team_workspace')
    expect(screen.getByRole('combobox', { name: 'Harness', exact: true })).toBeDisabled()
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toBeDisabled()
    expect(screen.getByText(/From PrivateHarnessInitializer/)).toBeVisible()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'composed')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Environment template'), 'team_workspace')
    await user.click(screen.getByRole('button', { name: 'Customize profile' }))
    await user.click(screen.getByRole('button', { name: 'Customize template' }))
    await user.selectOptions(screen.getByLabelText('Tool permission policy'), 'ask')
    await user.clear(screen.getByRole('textbox', { name: /Docker image/ }))
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'team:2')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'custom')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'local' })
    await user.selectOptions(screen.getByLabelText('Environment template'), 'local')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'local')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
    await user.selectOptions(screen.getByLabelText('Environment template'), '')
    await user.selectOptions(screen.getByLabelText('Environment template'), 'local')
    expect(screen.getByLabelText('I understand local tools can access the host')).not.toBeChecked()
  })

  it.each(['changed', 'removed', 'failed'])('blocks stale selected presets after a %s reload', async (outcome: string) => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue(presetCatalog())
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'team_harness' })
    await user.selectOptions(screen.getByLabelText('Harness profile'), 'team_harness')
    await user.selectOptions(screen.getByLabelText('Environment template'), 'team_workspace')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'test')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
    const next = presetCatalog()
    if (outcome === 'removed') next.harness_profiles.splice(0)
    if (outcome === 'changed') next.harness_profiles[0] = { ...next.harness_profiles[0],
      profile: { ...next.harness_profiles[0].profile, command: ['different', 'acp'] } }
    if (outcome === 'failed') jest.mocked(targetsApi.listAgentPresets).mockRejectedValueOnce(new Error('catalog offline'))
    else jest.mocked(targetsApi.listAgentPresets).mockResolvedValueOnce(next)
    await user.click(screen.getByRole('button', { name: 'Refresh options' }))
    await waitFor(() => expect(screen.getByRole('button', { name: 'Refresh options' })).toBeEnabled())
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    expect(screen.getByText(/selected preset is unavailable or changed/)).toBeVisible()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
    await user.click(screen.getByRole('button', { name: 'Customize profile' }))
    if (outcome === 'failed') await user.click(screen.getByRole('button', { name: 'Customize template' }))
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeEnabled()
  })

  it('reports unavailable initializer compositions without claiming they are targets', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      ...presetCatalog(), unavailable_targets: { optional_agent: 'Model target missing' },
    })
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.click(await screen.findByText('Unavailable initializer targets (1)'))
    expect(screen.getByText('optional_agent: Model target missing')).toBeVisible()
    expect(screen.getByText('Session-only')).toBeVisible()
  })

  it('preserves explicitly selected tool credentials when switching to BYOK', async () => {
    const user = userEvent.setup()
    const source = makeTarget({ target_registry_name: 'source' })
    source.inference_capabilities = {
      wire_apis: ['completions'], streaming: true, tool_calls: true, input_modalities: ['text'], blocked_reason: null,
    }
    jest.mocked(listRegisteredTargets).mockResolvedValue([source])
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'bound' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.click(screen.getByText('Launch and authentication'))
    await user.type(screen.getByLabelText('Harness credential references'), 'TEST_REPO_CREDENTIAL')
    await user.type(screen.getByLabelText('ACP authentication method'), 'copilot-login')
    await screen.findByRole('option', { name: 'source' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'source')
    expect(screen.getByLabelText('Harness credential references')).toHaveValue('TEST_REPO_CREDENTIAL')
    expect(screen.getByLabelText('ACP authentication method')).toHaveValue('copilot-login')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    expect(screen.getByText(/Remove the ACP authentication method explicitly/)).toBeVisible()
    await user.clear(screen.getByLabelText('ACP authentication method'))
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: /responses-only/ })
    expect(screen.getByRole('option', { name: /responses-only/ })).toBeEnabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'responses-only')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: /chat-only/ })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), '')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'chat-only')
    expect(screen.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('chat-only')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'opencode')
    expect(screen.queryByLabelText('Inference protocol')).not.toBeInTheDocument()
  })

  it('swaps to a registered adapter without prescribing an image or copying a directory', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'open', target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(screen.getByRole('textbox', { name: /Docker image/ })).toHaveValue('')
    await screen.findByRole('option', { name: 'OpenCode' })
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'opencode')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'open')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'operator-owned-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { harness_kind: 'opencode', command: ['opencode', 'acp'], credential_env: [] },
      environment_template: { image: 'operator-owned-image', workspace_base: 'image' },
    })
  })

  it('accepts an explicit custom ACP command with private local workspace', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'custom', target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'acp')
    await user.type(screen.getByLabelText('Executable'), 'test-agent')
    await user.type(screen.getByLabelText('Arguments'), '--stdio{enter}--no-login')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'custom')
    await user.selectOptions(screen.getByLabelText('Execution environment'), 'local')
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { harness_kind: 'acp', command: ['test-agent', '--stdio', '--no-login'] },
      environment_template: { environment: 'local', workspace_base: 'empty' },
    })
  })

  it('configures a registered environment provider from its settings schema and isolation claim', async () => {
    const user = userEvent.setup()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      harness_profiles: [], environment_templates: [], unavailable_targets: {}, harness_adapters: CATALOG_ADAPTERS,
      environment_providers: [
        ...BUILTIN_ENVIRONMENT_PROVIDERS,
        {
          name: 'team_sandbox', display_name: 'Team sandbox', description: 'Runs on the team pool.',
          capabilities: { isolated: false, checkpoint: false },
          settings_schema: {
            type: 'object', required: ['pool'],
            properties: {
              pool: { type: 'string', description: 'Pool name' },
              cpu_count: { type: 'integer', default: 2 },
              nested: { type: 'object' },
            },
          },
        },
      ],
    })
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_registry_name: 'pooled', target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'Team sandbox' })
    await user.selectOptions(screen.getByLabelText('Execution environment'), 'team_sandbox')
    expect(screen.getByText('Runs on the team pool.')).toBeVisible()
    expect(screen.queryByRole('textbox', { name: /Docker image/ })).not.toBeInTheDocument()
    expect(screen.queryByLabelText(/nested/)).not.toBeInTheDocument()
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'pooled')
    await user.type(screen.getByLabelText(/^pool/), 'blue')
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      environment_template: {
        environment: 'team_sandbox', environment_settings: { pool: 'blue', cpu_count: 2 },
        local_execution_acknowledged: true, image: null,
      },
    })
  })

  it('does not silently reinterpret a copied target whose environment provider is unavailable', async () => {
    const user = userEvent.setup()
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} initialConfiguration={{
      name: 'copied', model_binding: { model: '' },
      harness_profile: { harness_kind: 'copilot', command: ['copilot', '--acp', '--stdio'], permission_policy: 'deny',
        credential_env: [], authentication_method: null },
      environment_template: { environment: 'team_sandbox', environment_settings: { pool: 'blue' }, image: null, local_execution_acknowledged: false },
    }} /></TestWrapper>)
    expect(await screen.findByText(/Environment provider is unavailable/)).toBeVisible()
    expect(screen.getByLabelText('Execution environment')).toHaveValue('team_sandbox')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'copied')
    expect(screen.getByRole('button', { name: 'Create Target', exact: true })).toBeDisabled()
    expect(targetsApi.createTarget).not.toHaveBeenCalled()
  })

  beforeEach(() => {
    jest.clearAllMocks()
    jest.mocked(targetsApi.getStartupDestination).mockReset()
    jest.mocked(targetsApi.saveForStartup).mockReset()
    jest.mocked(useRuntime).mockReturnValue({ ready: true, state: 'ready', generation: 'initial' })
    jest.mocked(targetsApi.listAgentPresets).mockReset()
    jest.mocked(targetsApi.listAgentPresets).mockResolvedValue({
      harness_profiles: [], environment_templates: [], unavailable_targets: {}, harness_adapters: CATALOG_ADAPTERS,
    })
    jest.mocked(listRegisteredTargets).mockReset()
    jest.mocked(listRegisteredTargets).mockResolvedValue([])
  })

  it.each([false, true])('registers separate configuration primitives with Deny by default (select for chat: %s)', async (selectForChat: boolean) => {
    const target = makeTarget({ target_type: 'AgentTarget', target_registry_name: 'receipt' })
    jest.mocked(targetsApi.createTarget).mockResolvedValue(target)
    const onCreated = jest.fn()
    const user = userEvent.setup()
    render(<TestWrapper><AgentTargetForm selectForChat={selectForChat} onClose={jest.fn()} onCreated={onCreated} /></TestWrapper>)
    expect(screen.getByRole('region', { name: 'Model' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Harness' })).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Environment' })).toBeInTheDocument()
    expect(screen.getByLabelText('Tool permission policy')).toHaveValue('deny')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'receipt')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.type(screen.getByLabelText('Model ID'), 'test-model')
    await user.click(screen.getByRole('button', { name: selectForChat ? 'Create and select' : 'Create Target' }))
    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(target))
    expect(targetsApi.createTarget).toHaveBeenCalledWith({
      name: 'receipt', type: 'AgentTarget', params: { agent_configuration: expect.objectContaining({
        model_binding: { model: 'test-model' },
        harness_profile: expect.objectContaining({ permission_policy: 'deny' }),
        environment_template: expect.objectContaining({ environment: 'docker' }),
      }) },
    })
  })

  it.each(['deny', 'allow_once', 'ask'] as const)('requires explicit local acknowledgement and preserves a copied %s policy', async (permissionPolicy: HarnessProfile['permission_policy']) => {
    const initial: AgentTargetConfiguration = {
      name: 'custom', model_binding: { model: 'same' },
      harness_profile: { command: ['test-agent', '--stdio'], credential_env: [], authentication_method: null, permission_policy: permissionPolicy },
      environment_template: { environment: 'local', image: null, local_execution_acknowledged: true },
    }
    const user = userEvent.setup()
    jest.mocked(targetsApi.createTarget).mockResolvedValue(makeTarget({ target_type: 'AgentTarget' }))
    render(<TestWrapper><AgentTargetForm initialConfiguration={initial} onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    expect(screen.getByLabelText('Tool permission policy')).toHaveValue(permissionPolicy)
    expect(screen.getByRole('button', { name: 'Create Target' })).toBeDisabled()
    await user.click(screen.getByLabelText('I understand local tools can access the host'))
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      harness_profile: { command: ['test-agent', '--stdio'], authentication_method: null, permission_policy: permissionPolicy },
    })
    expect(initial.name).toBe('custom')
  })

  it('surfaces registration errors without dismissing the form', async () => {
    jest.mocked(targetsApi.createTarget).mockRejectedValue(new Error('Registration rejected'))
    const user = userEvent.setup()
    const onCreated = jest.fn()
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={onCreated} /></TestWrapper>)
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'receipt')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
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
    render(<TestWrapper><AgentTargetForm onClose={jest.fn()} onCreated={jest.fn()} /></TestWrapper>)
    await screen.findByRole('option', { name: 'source' })
    expect(screen.getByRole('option', { name: /unsupported — No inference connection/ })).toBeDisabled()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Model', exact: true }), 'source')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Harness', exact: true }), 'opencode')
    expect(screen.getByRole('combobox', { name: 'Model', exact: true })).toHaveValue('source')
    await user.type(screen.getByRole('textbox', { name: /Target name/ }), 'bound-agent')
    await user.type(screen.getByRole('textbox', { name: /Docker image/ }), 'test-image')
    await user.click(screen.getByRole('button', { name: 'Create Target' }))
    await waitFor(() => expect(targetsApi.createTarget).toHaveBeenCalled())
    expect(jest.mocked(targetsApi.createTarget).mock.calls[0][0].params.agent_configuration).toMatchObject({
      model_binding: { target_registry_name: 'source', target_identifier_hash: source.identifier.hash, wire_api: 'completions' },
      harness_profile: { harness_kind: 'opencode', credential_env: [], authentication_method: null },
    })
  })
})
