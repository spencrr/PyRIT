import { useEffect, useId, useState } from 'react'
import { createPortal } from 'react-dom'

import {
  Badge, Button, Checkbox, Field, Input, MessageBar, MessageBarBody, Select, Text, Textarea,
} from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { toApiError } from '@/services/errors'
import { useRuntime } from '@/hooks/useRuntime'
import type {
  AgentPresetCatalog, AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile,
  AgentStartupDestination, SavedAgentStartup, InferenceWireApi, RegisteredEnvironmentTemplate, RegisteredHarnessProfile, TargetInstance,
} from '@/types'
import ParameterField from '@/components/Parameters/ParameterField'
import { buildParametersFromForm, getInitialFormValues, type ParameterFormValue } from '@/components/Parameters/parameterForm'
import { targetEndpoint, targetModelName } from '@/utils/targetIdentity'

import { useAgentTargetFormStyles } from './AgentTargetForm.styles'
import { BUILTIN_HARNESS_ADAPTERS, modelBindingLabel, modelBindingOptions } from './agentModelBinding'

interface AgentTargetFormProps {
  readonly onClose: () => void
  readonly onCreated: (target: TargetInstance) => void
  readonly initialConfiguration?: AgentTargetConfiguration
  readonly selectForChat?: boolean
  readonly onBusyChange?: (busy: boolean) => void
  readonly actionsContainer?: HTMLDivElement | null
}

const ENVIRONMENT_LABELS: Record<string, string> = {
  docker_network: 'Network', docker_memory: 'Memory limit', docker_cpus: 'CPU cores', working_directory: 'Working directory',
}

export default function AgentTargetForm({
  onClose, onCreated, initialConfiguration, selectForChat = false, onBusyChange, actionsContainer,
}: AgentTargetFormProps) {
  const styles = useAgentTargetFormStyles()
  const formId = useId()
  const { generation, ready } = useRuntime()
  const [catalogState, setCatalogState] = useState<{ generation: string; catalog: AgentPresetCatalog } | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [reload, setReload] = useState(0)
  const [loadingCatalog, setLoadingCatalog] = useState(true)
  const [harnessPreset, setHarnessPreset] = useState<RegisteredHarnessProfile | null>(null)
  const [environmentPreset, setEnvironmentPreset] = useState<RegisteredEnvironmentTemplate | null>(null)
  const [harnessBase, setHarnessBase] = useState<HarnessProfile | undefined>(initialConfiguration?.harness_profile)
  const [environmentBase, setEnvironmentBase] = useState<EnvironmentTemplate | undefined>(initialConfiguration?.environment_template)
  const [name, setName] = useState(initialConfiguration ? `${initialConfiguration.name}-copy` : '')
  const [model, setModel] = useState(initialConfiguration?.model_binding.model ?? '')
  const [harness, setHarness] = useState(
    initialConfiguration?.harness_profile.harness_kind
      ?? (initialConfiguration ? 'acp' : 'copilot'),
  )
  const [command, setCommand] = useState(initialConfiguration?.harness_profile.command[0] ?? 'copilot')
  const [argumentsText, setArgumentsText] = useState(initialConfiguration?.harness_profile.command.slice(1).join('\n') ?? '--acp\n--stdio')
  const [authentication, setAuthentication] = useState(initialConfiguration?.harness_profile.authentication_method ?? '')
  const [source, setSource] = useState(initialConfiguration?.model_binding.target_registry_name ?? '')
  const [sourceHash, setSourceHash] = useState<string | null>(initialConfiguration?.model_binding.target_identifier_hash ?? null)
  const [wireApi, setWireApi] = useState<InferenceWireApi>(initialConfiguration?.model_binding.wire_api ?? 'completions')
  const [sourceState, setSourceState] = useState<{
    generation: string; reload: number; targets: TargetInstance[]
  } | null>(null)
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [captureInference, setCaptureInference] = useState(initialConfiguration?.capture_inference_content ?? false)
  const [maxInference, setMaxInference] = useState(String(initialConfiguration?.max_inference_requests ?? 100))
  const [environment, setEnvironment] = useState<'docker' | 'local'>(
    initialConfiguration?.environment_template.environment ?? 'docker',
  )
  const [image, setImage] = useState(initialConfiguration?.environment_template.image ?? '')
  const [workspaceBase, setWorkspaceBase] = useState<'image' | 'empty'>(
    initialConfiguration?.environment_template.workspace_base
      ?? (initialConfiguration?.environment_template.environment === 'local' ? 'empty' : 'image'),
  )
  const [environmentValues, setEnvironmentValues] = useState<Record<string, ParameterFormValue>>({})
  const [credential, setCredential] = useState(
    initialConfiguration?.harness_profile.credential_env.join('\n') ?? '',
  )
  const [permissionPolicy, setPermissionPolicy] = useState<'ask' | 'deny' | 'allow_once'>(
    initialConfiguration?.harness_profile.permission_policy ?? 'ask',
  )
  const [acknowledged, setAcknowledged] = useState(false)
  const [turnTimeout, setTurnTimeout] = useState(String(initialConfiguration?.turn_timeout_seconds ?? 180))
  const [idleTimeout, setIdleTimeout] = useState(String(initialConfiguration?.idle_timeout_seconds ?? 300))
  const [lifetime, setLifetime] = useState(String(initialConfiguration?.lifetime_seconds ?? 900))
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saveForStartup, setSaveForStartup] = useState(false)
  const [destinationState, setDestinationState] = useState<{
    generation: string; revision: number; destination: AgentStartupDestination
  } | null>(null)
  const [destinationRevision, setDestinationRevision] = useState(0)
  const [destinationError, setDestinationError] = useState<string | null>(null)
  const [saved, setSaved] = useState<SavedAgentStartup | null>(null)

  useEffect(() => {
    if (!saveForStartup || !ready) return
    let stopped = false
    targetsApi.getStartupDestination().then((destination) => {
      if (!stopped) { setDestinationState({ generation, revision: destinationRevision, destination }); setDestinationError(null) }
    }).catch((cause: unknown) => {
      if (!stopped) setDestinationError(toApiError(cause).detail)
    })
    return () => { stopped = true }
  }, [saveForStartup, ready, generation, destinationRevision])

  const destination = destinationState?.generation === generation && destinationState.revision === destinationRevision
    ? destinationState.destination : null
  const startupBlocked = saveForStartup && (!destination || Boolean(destinationError) || !/^[a-z][a-z0-9_]{0,63}$/.test(name.trim()))

  useEffect(() => {
    if (!ready) return
    let stopped = false
    listRegisteredTargets().then((targets: TargetInstance[]) => {
      if (!stopped) { setSourceState({ generation, reload, targets }); setSourceError(null) }
    }).catch((cause: unknown) => {
      if (!stopped) setSourceError(toApiError(cause).detail)
    })
    targetsApi.listAgentPresets().then((catalog) => {
      if (!stopped) { setCatalogState({ generation, catalog }); setCatalogError(null); setLoadingCatalog(false) }
    }).catch((cause: unknown) => {
      if (!stopped) { setCatalogError(toApiError(cause).detail); setLoadingCatalog(false) }
    })
    return () => { stopped = true }
  }, [generation, ready, reload])

  const catalog = catalogState?.generation === generation ? catalogState.catalog : null
  const catalogUnavailable = !ready || loadingCatalog || !catalog || Boolean(catalogError)
  const adapters = catalog?.harness_adapters ?? BUILTIN_HARNESS_ADAPTERS
  const adapterMissing = !adapters.some((item) => item.name === harness)
  const adapterBlocked = adapterMissing || (!BUILTIN_HARNESS_ADAPTERS.some((item) => item.name === harness) && catalogUnavailable)
  const harnessChanged = harnessPreset && (!catalog?.harness_profiles.some((item) => item.name === harnessPreset.name
    && JSON.stringify(item.profile) === JSON.stringify(harnessPreset.profile)))
  const environmentChanged = environmentPreset && (!catalog?.environment_templates.some((item) => item.name === environmentPreset.name
    && JSON.stringify(item.template) === JSON.stringify(environmentPreset.template)))
  const presetsBlocked = Boolean((harnessPreset || environmentPreset) && (catalogUnavailable || harnessChanged || environmentChanged))
  const requirements = harnessBase?.inference_requirements
  const sourcesCurrent = ready && sourceState?.generation === generation && sourceState.reload === reload
  const sources = sourcesCurrent ? sourceState.targets : []

  const eligibility = (target: TargetInstance): string | null => modelBindingOptions(target, harness, requirements, adapters).reason
  const selectedSource = sources.find((target: TargetInstance) => target.target_registry_name === source)
  const protocols = selectedSource ? modelBindingOptions(selectedSource, harness, requirements, adapters).protocols : []
  const sourceChanged = Boolean(selectedSource && selectedSource.identifier.hash !== sourceHash)
  const sourceBlocked = Boolean(source) && (!sourcesCurrent || !sourceHash || Boolean(sourceError)
    || !selectedSource || sourceChanged || eligibility(selectedSource) !== null || !protocols.includes(wireApi))
  const environmentParameters = catalog?.environment_parameters?.[environment] ?? []
  const knownEnvironmentFields = new Set(Object.values(catalog?.environment_parameters ?? {})
    .flatMap((parameters) => (parameters ?? []).map((parameter) => parameter.name)))
  const environmentEditsBlocked = !environmentPreset && Object.keys(environmentValues).length > 0
    && (catalogUnavailable || environmentParameters.length === 0
      || Object.keys(environmentValues).some((name) => !knownEnvironmentFields.has(name)))
  const environmentFormValues = {
    ...getInitialFormValues(environmentParameters, { ...environmentBase }),
    ...environmentValues,
  }

  const selectModelSource = (name: string): void => {
    if (!name) { setSource(''); setSourceHash(null); return }
    const selected = sources.find((target) => target.target_registry_name === name)
    if (!sourcesCurrent || sourceError || !selected || eligibility(selected) !== null) {
      setError('Model source is unavailable or incompatible. Reload sources before selecting it.')
      return
    }
    setSource(name)
    setSourceHash(selected.identifier.hash)
    const options = modelBindingOptions(selected, harness, requirements, adapters).protocols
    if (!options.includes(wireApi)) setWireApi(options[0])
  }

  const selectHarness = (kind: string): void => {
    const adapter = adapters.find((item) => item.name === kind)
    if (!adapter) { setError('Harness adapter is unavailable. Refresh options.'); return }
    setHarnessBase(undefined)
    setHarness(kind)
    setCommand(adapter.default_command[0] ?? '')
    setArgumentsText(adapter.default_command.slice(1).join('\n'))
    setAuthentication('')
    setCredential('')
    if (selectedSource) {
      const options = modelBindingOptions(selectedSource, kind, undefined, adapters).protocols
      if (options.length === 1) setWireApi(options[0])
    }
  }

  const selectHarnessPreset = (name: string): void => {
    const preset = catalog?.harness_profiles.find((item) => item.name === name)
    if (!name) { setHarnessPreset(null); return }
    if (!preset) { setError('Harness profile is unavailable. Refresh options.'); return }
    const profile = preset.profile
    setHarnessPreset(preset)
    setHarnessBase(profile)
    setHarness(profile.harness_kind ?? 'acp')
    setCommand(profile.command[0])
    setArgumentsText(profile.command.slice(1).join('\n'))
    setCredential(profile.credential_env.join('\n'))
    setAuthentication(profile.authentication_method ?? '')
    setPermissionPolicy(profile.permission_policy)
    if (selectedSource) {
      const options = modelBindingOptions(selectedSource, profile.harness_kind, profile.inference_requirements, adapters).protocols
      if (options.length === 1) setWireApi(options[0])
    }
  }

  const selectEnvironmentPreset = (name: string): void => {
    const preset = catalog?.environment_templates.find((item) => item.name === name)
    if (!name) { setEnvironmentPreset(null); return }
    if (!preset) { setError('Environment template is unavailable. Refresh options.'); return }
    const template = preset.template
    setEnvironmentPreset(preset)
    setEnvironmentBase(template)
    setEnvironment(template.environment)
    setImage(template.image ?? '')
    setWorkspaceBase(template.workspace_base ?? (template.environment === 'local' ? 'empty' : 'image'))
    setEnvironmentValues({})
    setAcknowledged(false)
  }

  const reloadPresets = (): void => {
    setLoadingCatalog(true)
    setReload((value) => value + 1)
  }

  const submit = async (): Promise<void> => {
    if (busy) return
    if (startupBlocked) { setError('Select a current startup destination and use a lowercase startup name.'); return }
    if (environmentEditsBlocked) {
      setError('Environment edits are preserved. Refresh their settings before creating the target.')
      return
    }
    if (!ready || adapterBlocked || sourceBlocked || presetsBlocked || (source && authentication.trim())) {
      setError('Selected model or preset settings are unavailable or changed. Confirm your selection before registering.')
      return
    }
    if (environment === 'local' && !acknowledged) {
      setError('Acknowledge local host access before creating the target.')
      return
    }
    const parsedEnvironment = buildParametersFromForm(environmentParameters, environmentFormValues)
    if (!environmentPreset && !parsedEnvironment.ok) { setError(parsedEnvironment.error); return }
    setBusy(true)
    onBusyChange?.(true)
    setError(null)
    try {
      const configuration: AgentTargetConfiguration = {
        ...initialConfiguration,
        name: name.trim(),
        model_binding: source ? {
          model: model.trim(), target_registry_name: source,
          target_identifier_hash: sourceHash, wire_api: wireApi,
        } : { model: model.trim() },
        harness_profile: harnessPreset?.profile ?? {
          ...harnessBase,
          harness_kind: harness,
          command: [command.trim(), ...argumentsText.split('\n').filter((value) => value.length > 0)],
          authentication_method: authentication.trim() || null,
          credential_env: credential.split(/[\n,]/).map((value: string) => value.trim()).filter(Boolean),
          permission_policy: permissionPolicy,
          inference_requirements: {
            wire_api: wireApi, streaming: true, tool_calls: true, input_modalities: ['text'],
            ...harnessBase?.inference_requirements,
          },
        },
        environment_template: environmentPreset ? {
          ...environmentPreset.template,
          local_execution_acknowledged: environmentPreset.template.environment === 'local' && acknowledged,
        } : {
          ...environmentBase,
          ...(parsedEnvironment.ok ? parsedEnvironment.parameters : {}),
          environment, image: environment === 'docker' ? image.trim() : null,
          workspace_base: environment === 'local' ? 'empty' : workspaceBase,
          local_execution_acknowledged: environment === 'local' && acknowledged,
        },
        turn_timeout_seconds: Number(turnTimeout), idle_timeout_seconds: Number(idleTimeout),
        lifetime_seconds: Number(lifetime),
        capture_inference_content: captureInference,
        max_inference_requests: Number(maxInference),
      }
      const selection = (harnessPreset || environmentPreset) ? { agent_presets: {
          harness_profile: harnessPreset?.name ?? null, environment_template: environmentPreset?.name ?? null,
      } } : {}
      if (saveForStartup && destination) {
        const result = await targetsApi.saveForStartup({
          name: name.trim(), configuration, version: destination.version, ...selection,
        })
        setSaved(result)
        setDestinationState({ generation, revision: destinationRevision, destination: result })
      } else {
        const target = await targetsApi.createTarget({
          name: configuration.name, type: 'AgentTarget', params: { agent_configuration: configuration }, ...selection,
        })
        onCreated(target)
      }
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
    } finally { setBusy(false); onBusyChange?.(false) }
  }

  const actions = (
    <div className={styles.actions}>
      <Button onClick={onClose} disabled={busy}>{saved ? 'Close' : 'Cancel'}</Button>
      <Button form={formId} type="submit" appearance="primary" disabled={busy || !ready || adapterBlocked || startupBlocked || environmentEditsBlocked || presetsBlocked || sourceBlocked || (Boolean(source) && Boolean(authentication.trim())) || !command.trim()
        || !name.trim() || (environment === 'docker' && !image.trim()) || (environment === 'local' && !acknowledged)}>
        {busy ? saveForStartup ? 'Saving...' : 'Creating...' : saveForStartup ? 'Save for startup' : selectForChat ? 'Create and select' : 'Create Target'}
      </Button>
    </div>
  )

  return (
    <form id={formId} className={styles.form} onSubmit={(event) => { event.preventDefault(); void submit() }}>
      <fieldset className={styles.settingsFields} disabled={busy}>
        {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
        {saved && <MessageBar intent="success">
          Saved {saved.name} for the next backend start. No target was registered or started; existing sessions are unchanged.
        </MessageBar>}
        <div className={styles.toolbar}>
          <Badge appearance="outline">{saveForStartup ? 'Startup configuration' : 'Session-only'}</Badge>
          <Button className={styles.presetAction} appearance="subtle" onClick={reloadPresets} disabled={loadingCatalog || !ready}>Refresh options</Button>
        </div>
        {catalogError && <MessageBar intent="error">Presets unavailable: {catalogError}. Refresh or use custom settings.</MessageBar>}
        {adapterBlocked && <MessageBar intent="error">Harness adapter is unavailable. Refresh options or install its trusted initializer before creating this target.</MessageBar>}
        {presetsBlocked && <MessageBar intent="warning">A selected preset is unavailable or changed. Accept its update or customize the copied settings.</MessageBar>}
        {environmentEditsBlocked && <MessageBar intent="warning">Environment edits are preserved, but their settings could not be verified. Refresh options before creating the target.</MessageBar>}
        <Field label="Target name" required hint={saveForStartup ? 'Startup names use lowercase letters, digits and underscores, starting with a letter.' : undefined}>
          <Input value={name} onChange={(_, data) => setName(data.value)} required />
        </Field>
        <div className={styles.composition}>
          <section className={styles.section} aria-label="Model">
            <Text weight="semibold">Model</Text>
            <Field label="Model" hint="Choose a registered model connection or let the harness choose.">
              <Select className={styles.select} value={source} disabled={!ready} onChange={(_, data) => selectModelSource(data.value)}>
                <option value="">Use harness default</option>
                {source && !selectedSource && <option value={source} disabled>{source} — {sourcesCurrent ? 'unavailable' : 'checking configuration'}</option>}
                {sources.map((target) => <option key={target.target_registry_name} value={target.target_registry_name} disabled={eligibility(target) !== null}>
                  {modelBindingLabel(target)}{eligibility(target) ? ` — ${eligibility(target)}` : ''}
                </option>)}
              </Select>
            </Field>
            {!source && <Field label="Model ID" hint="Optional. Blank uses the harness default.">
              <Input value={model} onChange={(_, data) => setModel(data.value)} />
            </Field>}
            {sourceError && <MessageBar intent="warning">{sourceError}</MessageBar>}
            {sourceChanged && <MessageBar intent="warning">This model connection changed. Confirm the replacement before creating the target.</MessageBar>}
            {sourceChanged && sourcesCurrent && !sourceError && selectedSource && eligibility(selectedSource) === null
              && <Button className={styles.presetAction} onClick={() => selectModelSource(source)}>Use updated model source</Button>}
            {sourceBlocked && <MessageBar intent="error">Selected model source is unavailable or incompatible.</MessageBar>}
            {source && <details open={protocols.length > 1 || (protocols.length > 0 && !protocols.includes(wireApi))}>
              <summary>Connection details</summary>
              <Text block size={200}>Target: {source}</Text>
              <Text block size={200}>Deployment: {selectedSource ? targetModelName(selectedSource) || 'Not specified' : 'Unavailable'}</Text>
              {selectedSource && targetEndpoint(selectedSource) && <Text block size={200}>{targetEndpoint(selectedSource)}</Text>}
              {protocols.length > 1 ? <Field label="Inference protocol">
                <Select className={styles.select} value={wireApi} onChange={(_, data) => setWireApi(data.value === 'responses' ? 'responses' : 'completions')}>
                  {protocols.map((api) => <option key={api} value={api}>{api === 'responses' ? 'Responses' : 'Chat Completions'}</option>)}
                </Select>
              </Field> : <Text block size={200}>Protocol: {wireApi === 'responses' ? 'Responses' : 'Chat Completions'}</Text>}
              {protocols.length === 1 && !protocols.includes(wireApi) && <Button onClick={() => setWireApi(protocols[0])}>Use compatible protocol</Button>}
              <Field label="Harness model hint" hint="Optional base-model identity for the harness; does not change the provider deployment.">
                <Input value={model} onChange={(_, data) => setModel(data.value)} />
              </Field>
              <Text block size={200}>Provider keys are not forwarded. Local execution does not isolate credentials.</Text>
              <Text block size={200}>Configured model-target settings, including temperature and output limits, override harness suggestions.</Text>
            </details>}
          </section>
          <section className={styles.section} aria-label="Harness">
            <Text weight="semibold">Harness</Text>
            <Field label="Harness profile">
              <Select className={styles.select} value={harnessPreset?.name ?? ''} disabled={catalogUnavailable} onChange={(_, data) => selectHarnessPreset(data.value)}>
                <option value="">Custom</option>
                {harnessPreset && !catalog?.harness_profiles.some((item) => item.name === harnessPreset.name) && <option value={harnessPreset.name}>{harnessPreset.name} (unavailable)</option>}
                {catalog?.harness_profiles.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
              </Select>
            </Field>
            {harnessPreset && <>
              <Text size={200}>From {harnessPreset.initializer ?? 'Python registration'}</Text>
              {harnessChanged && !catalogUnavailable && catalog?.harness_profiles.some((item) => item.name === harnessPreset.name)
                && <Button className={styles.presetAction} onClick={() => selectHarnessPreset(harnessPreset.name)}>Use updated profile</Button>}
              <Button className={styles.presetAction} onClick={() => setHarnessPreset(null)}>Customize profile</Button>
            </>}
            <fieldset className={styles.settingsFields} disabled={Boolean(harnessPreset)}>
              <Field label="Harness">
                <Select className={styles.select} value={harness} onChange={(_, data) => selectHarness(data.value)}>
                  {adapterMissing && <option value={harness} disabled>{harness} (unavailable)</option>}
                  {adapters.map((adapter) => <option key={adapter.name} value={adapter.name}>{adapter.display_name}</option>)}
                </Select>
              </Field>
              <Field label="Tool permission policy">
                <Select className={styles.select} value={permissionPolicy} onChange={(_, data) => setPermissionPolicy(data.value === 'allow_once' ? 'allow_once' : data.value === 'deny' ? 'deny' : 'ask')}>
                  <option value="ask">Ask me</option><option value="deny">Deny requests</option><option value="allow_once">Allow requests</option>
                </Select>
              </Field>
              {harness === 'copilot' && !source && !credential.trim()
                && <Text size={200}>A model ID does not authenticate Copilot. Provide a token environment reference below (for development, export COPILOT_GITHUB_TOKEN on the backend from your GitHub CLI login).</Text>}
              <details open={harness === 'acp' || Boolean(source && authentication.trim())}>
                <summary>Launch and authentication</summary>
                <Field label="Executable"><Input value={command} onChange={(_, data) => setCommand(data.value)} required /></Field>
                <Field label="Arguments" hint="One argument per line. No shell expansion."><Textarea value={argumentsText} onChange={(_, data) => setArgumentsText(data.value)} /></Field>
                <Field label="Harness credential references" hint="Environment variable names, one per line. Values are exposed to the tested agent and its tools.">
                  <Textarea value={credential} onChange={(_, data) => setCredential(data.value)} />
                </Field>
                <Field label="ACP authentication method" hint="Optional advertised method; not supported with current BYOK adapters.">
                  <Input value={authentication} onChange={(_, data) => setAuthentication(data.value)} />
                </Field>
                <Text size={200}>No stored home or login files are copied. Local processes may still discover same-user credentials. Permission policy only answers requests the harness raises.</Text>
              </details>
            </fieldset>
            {source && authentication.trim() && <MessageBar intent="error">Remove the ACP authentication method explicitly before using BYOK.</MessageBar>}
          </section>
          <section className={styles.section} aria-label="Environment">
            <Text weight="semibold">Environment</Text>
            <Field label="Environment template">
              <Select className={styles.select} value={environmentPreset?.name ?? ''} disabled={catalogUnavailable} onChange={(_, data) => selectEnvironmentPreset(data.value)}>
                <option value="">Custom</option>
                {environmentPreset && !catalog?.environment_templates.some((item) => item.name === environmentPreset.name) && <option value={environmentPreset.name}>{environmentPreset.name} (unavailable)</option>}
                {catalog?.environment_templates.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
              </Select>
            </Field>
            {environmentPreset && <>
              <Text size={200}>From {environmentPreset.initializer ?? 'Python registration'}</Text>
              {environmentChanged && !catalogUnavailable && catalog?.environment_templates.some((item) => item.name === environmentPreset.name)
                && <Button className={styles.presetAction} onClick={() => selectEnvironmentPreset(environmentPreset.name)}>Use updated template</Button>}
              <Button className={styles.presetAction} onClick={() => setEnvironmentPreset(null)}>Customize template</Button>
            </>}
            <fieldset className={styles.settingsFields} disabled={Boolean(environmentPreset)}>
              <Field label="Execution environment">
                <Select className={styles.select} value={environment} onChange={(_, data) => { setEnvironment(data.value === 'local' ? 'local' : 'docker'); setAcknowledged(false) }}>
                  <option value="docker">Docker</option><option value="local">Local (not sandboxed)</option>
                </Select>
              </Field>
              {environment === 'docker' && <Field label="Docker image" required><Input value={image} onChange={(_, data) => setImage(data.value)} required /></Field>}
              <details>
                <summary>Environment settings</summary>
                {environment === 'docker' && <Field label="Starting workspace">
                  <Select className={styles.select} value={workspaceBase} onChange={(_, data) => setWorkspaceBase(data.value === 'empty' ? 'empty' : 'image')}>
                    <option value="image">Image-provided</option><option value="empty">Empty</option>
                  </Select>
                </Field>}
                {environmentParameters.map((parameter) => <ParameterField key={parameter.name} parameter={parameter}
                  label={ENVIRONMENT_LABELS[parameter.name] ?? parameter.name.replace(/_/g, ' ')}
                  value={environmentFormValues[parameter.name] ?? ''} disabled={busy || Boolean(environmentPreset)}
                  onChange={(name, value) => setEnvironmentValues((previous) => ({ ...previous, [name]: value }))} />)}
                {!environmentParameters.length && <Text size={200}>Additional settings unavailable; existing values will be preserved.</Text>}
                {environmentBase?.fixture_directory && <Text size={200}>Workspace initialization is retained from the original configuration.</Text>}
                {source && environment === 'docker' && <Text size={200}>The container must be able to reach the backend model relay.</Text>}
              </details>
            </fieldset>
            {environment === 'local' && <Checkbox label="I understand local tools can access the host"
              checked={acknowledged} onChange={(_, data) => setAcknowledged(data.checked === true)} />}
          </section>
        </div>
        <details>
          <summary>Execution settings</summary>
          <div className={styles.composition}>
            <Field label="Turn deadline (seconds)"><Input type="number" min={1} max={3600} value={turnTimeout} onChange={(_, data) => setTurnTimeout(data.value)} required /></Field>
            <Field label="Idle expiry (seconds)"><Input type="number" min={1} max={86400} value={idleTimeout} onChange={(_, data) => setIdleTimeout(data.value)} required /></Field>
            <Field label="Maximum lifetime (seconds)"><Input type="number" min={1} max={86400} value={lifetime} onChange={(_, data) => setLifetime(data.value)} required /></Field>
            {source && <>
              <Field label="Maximum inference requests per execution"><Input type="number" min={1} max={100} value={maxInference} onChange={(_, data) => setMaxInference(data.value)} /></Field>
              <Checkbox label="Retain inference content (sensitive)" checked={captureInference} onChange={(_, data) => setCaptureInference(data.checked === true)} />
            </>}
          </div>
          {(initialConfiguration?.artifact_paths?.length ?? 0) > 0 && <Text size={200}>Artifact retention settings are unchanged.</Text>}
        </details>
        {catalog && Object.keys(catalog.unavailable_targets).length > 0 && <details>
          <summary>Unavailable initializer targets ({Object.keys(catalog.unavailable_targets).length})</summary>
          {Object.entries(catalog.unavailable_targets).map(([name, reason]) => <Text block key={name}>{name}: {reason}</Text>)}
        </details>}
        <Checkbox label="Save for startup instead of creating a session target" checked={saveForStartup}
          onChange={(_, data) => { setSaveForStartup(data.checked === true); setSaved(null); setError(null) }} />
        {saveForStartup && <section className={styles.section} aria-label="Startup destination">
          <Text weight="semibold">Startup configuration</Text>
          <Text size={200}>{destination?.source ?? 'Loading destination...'}</Text>
          <Text size={200}>Appends copies of these settings as initializer entries. It does not edit private initializer code or apply changes to this runtime.</Text>
          <Text size={200}>Keep secrets in credential references, never in commands or other fields. Existing startup names cannot be overwritten here.</Text>
          {source && <Text size={200}>The model target "{source}" must be recreated by an earlier configured initializer with the same identity. Its definition and credentials are not copied.</Text>}
          {destinationError && <MessageBar intent="error">{destinationError}</MessageBar>}
          <Button className={styles.presetAction} onClick={() => { setDestinationRevision((value) => value + 1); setDestinationError(null) }}>
            Reload destination
          </Button>
        </section>}
      </fieldset>
      {actionsContainer ? createPortal(actions, actionsContainer) : actionsContainer === undefined ? actions : null}
    </form>
  )
}
