import { useEffect, useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface,
  DialogTitle, Field, Input, MessageBar, MessageBarBody, Select, Text, Textarea,
} from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { toApiError } from '@/services/errors'
import { useRuntime } from '@/hooks/useRuntime'
import type {
  AgentPresetCatalog, AgentTargetConfiguration, EnvironmentTemplate, HarnessProfile,
  RegisteredEnvironmentTemplate, RegisteredHarnessProfile, TargetInstance,
} from '@/types'

import { useAgentTargetDialogStyles } from './AgentTargetDialog.styles'

interface AgentTargetDialogProps {
  readonly onClose: () => void
  readonly onCreated: (target: TargetInstance) => void
  readonly initialConfiguration?: AgentTargetConfiguration
  readonly selectForChat?: boolean
  readonly onBack?: () => void
}

export default function AgentTargetDialog({
  onClose, onCreated, initialConfiguration, selectForChat = false, onBack,
}: AgentTargetDialogProps) {
  const styles = useAgentTargetDialogStyles()
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
  const [harness, setHarness] = useState<'copilot' | 'opencode' | 'acp'>(
    initialConfiguration?.harness_profile.harness_kind
      ?? (initialConfiguration ? 'acp' : 'copilot'),
  )
  const [command, setCommand] = useState(initialConfiguration?.harness_profile.command[0] ?? 'copilot')
  const [argumentsText, setArgumentsText] = useState(initialConfiguration?.harness_profile.command.slice(1).join('\n') ?? '--acp\n--stdio')
  const [authentication, setAuthentication] = useState(initialConfiguration?.harness_profile.authentication_method ?? '')
  const [source, setSource] = useState(initialConfiguration?.model_binding.target_registry_name ?? '')
  const [sourceHash, setSourceHash] = useState<string | null>(initialConfiguration?.model_binding.target_identifier_hash ?? null)
  const [wireApi, setWireApi] = useState<'completions' | 'responses'>(initialConfiguration?.model_binding.wire_api ?? 'completions')
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
  const [workingDirectory, setWorkingDirectory] = useState(initialConfiguration?.environment_template.working_directory ?? '.')
  const [fixture, setFixture] = useState(initialConfiguration?.environment_template.fixture_directory ?? '')
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
  const [artifacts, setArtifacts] = useState(initialConfiguration?.artifact_paths?.join(', ') ?? '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

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
  const harnessChanged = harnessPreset && (!catalog?.harness_profiles.some((item) => item.name === harnessPreset.name
    && JSON.stringify(item.profile) === JSON.stringify(harnessPreset.profile)))
  const environmentChanged = environmentPreset && (!catalog?.environment_templates.some((item) => item.name === environmentPreset.name
    && JSON.stringify(item.template) === JSON.stringify(environmentPreset.template)))
  const presetsBlocked = Boolean((harnessPreset || environmentPreset) && (catalogUnavailable || harnessChanged || environmentChanged))
  const requirements = harnessBase?.inference_requirements
  const sourcesCurrent = ready && sourceState?.generation === generation && sourceState.reload === reload
  const sources = sourcesCurrent ? sourceState.targets : []

  const eligibility = (target: TargetInstance): string | null => {
    const capabilities = target.inference_capabilities
    if (!capabilities) return 'No inference implementation'
    if (capabilities.blocked_reason) return capabilities.blocked_reason
    if (!capabilities.wire_apis.includes(wireApi)) return `Does not support ${wireApi}`
    if ((requirements?.streaming !== false && !capabilities.streaming)
      || (requirements?.tool_calls !== false && !capabilities.tool_calls)) return 'Streaming and tool passthrough required'
    const required = requirements?.input_modalities ?? ['text']
    if (required.some((modality: string) => !capabilities.input_modalities.includes(modality))) return 'Missing required input modality'
    return null
  }
  const selectedSource = sources.find((target: TargetInstance) => target.target_registry_name === source)
  const sourceChanged = Boolean(selectedSource && selectedSource.identifier.hash !== sourceHash)
  const sourceBlocked = Boolean(source) && (!sourcesCurrent || !sourceHash || Boolean(sourceError)
    || !selectedSource || sourceChanged || eligibility(selectedSource) !== null)
  const harnessBlocked = Boolean(source) && (harness === 'acp' || (harness === 'opencode' && wireApi !== 'completions'))

  const selectModelSource = (name: string): void => {
    if (!name) { setSource(''); setSourceHash(null); return }
    const selected = sources.find((target) => target.target_registry_name === name)
    if (!sourcesCurrent || sourceError || !selected || eligibility(selected) !== null) {
      setError('Model source is unavailable or incompatible. Reload sources before selecting it.')
      return
    }
    setSource(name)
    setSourceHash(selected.identifier.hash)
  }

  const selectHarness = (kind: 'copilot' | 'opencode' | 'acp'): void => {
    setHarnessBase(undefined)
    setHarness(kind)
    setCommand(kind === 'acp' ? '' : kind)
    setArgumentsText(kind === 'copilot' ? '--acp\n--stdio' : kind === 'opencode' ? 'acp' : '')
    setAuthentication('')
    setCredential('')
  }

  const selectHarnessPreset = (name: string): void => {
    const preset = catalog?.harness_profiles.find((item) => item.name === name)
    if (!name) { setHarnessPreset(null); return }
    if (!preset) { setError('Harness profile is unavailable. Reload presets.'); return }
    const profile = preset.profile
    setHarnessPreset(preset)
    setHarnessBase(profile)
    setHarness(profile.harness_kind ?? 'acp')
    setCommand(profile.command[0])
    setArgumentsText(profile.command.slice(1).join('\n'))
    setCredential(profile.credential_env.join('\n'))
    setAuthentication(profile.authentication_method ?? '')
    setPermissionPolicy(profile.permission_policy)
  }

  const selectEnvironmentPreset = (name: string): void => {
    const preset = catalog?.environment_templates.find((item) => item.name === name)
    if (!name) { setEnvironmentPreset(null); return }
    if (!preset) { setError('Environment template is unavailable. Reload presets.'); return }
    const template = preset.template
    setEnvironmentPreset(preset)
    setEnvironmentBase(template)
    setEnvironment(template.environment)
    setImage(template.image ?? '')
    setWorkspaceBase(template.workspace_base ?? (template.environment === 'local' ? 'empty' : 'image'))
    setWorkingDirectory(template.working_directory ?? '.')
    setFixture(template.fixture_directory ?? '')
    setAcknowledged(false)
  }

  const reloadPresets = (): void => {
    setLoadingCatalog(true)
    setReload((value) => value + 1)
  }

  const submit = async (): Promise<void> => {
    if (sourceBlocked || presetsBlocked) {
      setError('Selected model or preset settings are unavailable or changed. Confirm your selection before registering.')
      return
    }
    setBusy(true)
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
          environment, image: environment === 'docker' ? image.trim() : null,
          workspace_base: environment === 'local' ? 'empty' : workspaceBase,
          working_directory: workingDirectory.trim() || '.',
          fixture_directory: fixture.trim() || null,
          expected_fixture_sha256: fixture === (environmentBase?.fixture_directory ?? '')
            ? environmentBase?.expected_fixture_sha256 : null,
          local_execution_acknowledged: environment === 'local' && acknowledged,
        },
        turn_timeout_seconds: Number(turnTimeout), idle_timeout_seconds: Number(idleTimeout),
        lifetime_seconds: Number(lifetime),
        artifact_paths: artifacts.split(',').map((value: string) => value.trim()).filter(Boolean),
        capture_inference_content: captureInference,
        max_inference_requests: Number(maxInference),
      }
      const target = await targetsApi.createTarget({
        name: configuration.name, type: 'AgentTarget', params: { agent_configuration: configuration },
        ...((harnessPreset || environmentPreset) ? { agent_presets: {
          harness_profile: harnessPreset?.name ?? null, environment_template: environmentPreset?.name ?? null,
        } } : {}),
      })
      onCreated(target)
    } catch (cause: unknown) {
      setError(toApiError(cause).detail)
    } finally { setBusy(false) }
  }

  return (
    <Dialog open onOpenChange={(_, data) => { if (!data.open && !busy) onClose() }}>
      <DialogSurface className={styles.surface}>
        <DialogBody>
          <DialogTitle>Create Target · Tool-capable harness</DialogTitle>
          <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void submit() }}>
            <DialogContent className={styles.content}>
              {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              <Text>No resources are provisioned until the first send. This target is reusable in the current backend runtime.</Text>
              <Text size={200}>Initializer presets are read-only starting settings. Registration here is session-only; it does not update your initializers or save a definition.</Text>
              <Button className={styles.presetAction} onClick={reloadPresets} disabled={busy || loadingCatalog || !ready}>Reload presets</Button>
              {catalogError && <MessageBar intent="error"><MessageBarBody>Presets unavailable: {catalogError}. Retry or choose custom configuration explicitly.</MessageBarBody></MessageBar>}
              {presetsBlocked && <MessageBar intent="warning"><MessageBarBody>A selected preset is unavailable or changed. Reselect it after reload, or customize the copied settings.</MessageBarBody></MessageBar>}
              {catalog && Object.keys(catalog.unavailable_targets).length > 0 && <details>
                <summary>Unavailable initializer targets ({Object.keys(catalog.unavailable_targets).length})</summary>
                {Object.entries(catalog.unavailable_targets).map(([name, reason]) => <Text block key={name}>{name}: {reason}</Text>)}
              </details>}
              <Field label="Target name" required>
                <Input value={name} onChange={(_, data) => setName(data.value)} required />
              </Field>
              <div className={styles.composition}>
              <section className={styles.section} aria-label="Harness profile">
                <Text weight="semibold">Harness profile</Text>
                <Field label="Registered harness profile">
                  <Select className={styles.select} value={harnessPreset?.name ?? ''}
                    disabled={busy || catalogUnavailable} onChange={(_, data) => selectHarnessPreset(data.value)}>
                    <option value="">Custom configuration</option>
                    {harnessPreset && !catalog?.harness_profiles.some((item) => item.name === harnessPreset.name)
                      && <option value={harnessPreset.name}>{harnessPreset.name} (unavailable)</option>}
                    {catalog?.harness_profiles.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                  </Select>
                </Field>
                {harnessPreset ? <>
                  <Text size={200}>Registered by {harnessPreset.initializer ?? 'Python registration'}. Settings are copied, not edited at source.</Text>
                  {harnessChanged && !catalogUnavailable && catalog?.harness_profiles.some((item) => item.name === harnessPreset.name)
                    && <Button className={styles.presetAction} disabled={busy} onClick={() => selectHarnessPreset(harnessPreset.name)}>Use updated profile</Button>}
                  <Button className={styles.presetAction} disabled={busy} onClick={() => setHarnessPreset(null)}>Customize profile</Button>
                </> : <Text size={200}>{catalog?.harness_profiles.length === 0 ? 'No registered profiles. Configure a harness_profile initializer or enter custom settings.' : 'Custom settings for this target.'}</Text>}
                <fieldset className={styles.settingsFields} disabled={busy || Boolean(harnessPreset)}>
                <Field label="Harness">
                  <Select className={styles.select} value={harness} onChange={(_, data) => selectHarness(
                    data.value === 'opencode' ? 'opencode' : data.value === 'acp' ? 'acp' : 'copilot',
                  )}>
                    <option value="copilot">GitHub Copilot · ACP</option>
                    <option value="opencode">OpenCode · ACP</option>
                    <option value="acp">Custom ACP harness</option>
                  </Select>
                </Field>
                <Text size={200}>For tools-off Copilot text or judge calls, choose GitHubCopilotTarget in the target type picker.</Text>
                <details open={harness === 'acp'}>
                  <summary>Launch and authentication</summary>
                  <Field label="Executable" hint="Runs in the selected environment, without a shell.">
                    <Input value={command} onChange={(_, data) => setCommand(data.value)} required />
                  </Field>
                  <Field label="Arguments" hint="One argument per line. Do not include secrets.">
                    <Textarea value={argumentsText} onChange={(_, data) => setArgumentsText(data.value)} />
                  </Field>
                    <Field label="Harness credential references" hint="One backend environment-variable name per line, never token values. These credentials are exposed to the tested agent and its tools, including in BYOK mode. Use narrowly scoped credentials.">
                      <Textarea value={credential} onChange={(_, data) => setCredential(data.value)} />
                    </Field>
                    <Field label="ACP authentication method" hint="Optional advertised method for harness-managed access. Current BYOK adapters require this to be empty; remove it explicitly when changing access mode.">
                      <Input value={authentication} onChange={(_, data) => setAuthentication(data.value)} />
                    </Field>
                </details>
                <Field label="Tool permission policy">
                  <Select className={styles.select} value={permissionPolicy} onChange={(_, data) => setPermissionPolicy(
                    data.value === 'allow_once' ? 'allow_once' : data.value === 'deny' ? 'deny' : 'ask',
                  )}>
                    <option value="ask">Ask operator (interactive chat)</option>
                    <option value="deny">Deny all requests (automation compatible)</option>
                    <option value="allow_once">Allow all requests once (automation compatible)</option>
                  </Select>
                </Field>
                </fieldset>
                <Text size={200}>This policy answers permission requests raised by the harness. OpenCode asks by default; workspace or agent configuration can change that behavior. Approval is not a sandbox or a tool allowlist.</Text>
              </section>
              <section className={styles.section} aria-label="Model binding">
                <Text weight="semibold">Model binding</Text>
                {harness !== 'acp' && <Field label="Inference protocol">
                  <Select className={styles.select} value={wireApi} onChange={(_, data) => setWireApi(data.value === 'responses' ? 'responses' : 'completions')}>
                    <option value="completions">Chat Completions</option>
                    <option value="responses" disabled={harness === 'opencode'}>Responses</option>
                  </Select>
                </Field>}
                <Field label="Model source">
                  <Select className={styles.select} value={source} disabled={busy || !ready} onChange={(_, data) => selectModelSource(data.value)}>
                    <option value="">Harness-managed access</option>
                    {source && !selectedSource && <option value={source} disabled>
                      {source} — {sourcesCurrent ? 'unavailable' : 'checking current configuration'}
                    </option>}
                    {sources.map((target: TargetInstance) => <option key={target.target_registry_name}
                      value={target.target_registry_name} disabled={harness === 'acp' || eligibility(target) !== null}>
                      {target.target_registry_name}{harness === 'acp' ? ' — BYOK adapter required'
                        : eligibility(target) ? ` — ${eligibility(target)}` : ' — inference compatible'}
                    </option>)}
                  </Select>
                </Field>
                {sourceError && <MessageBar intent="warning">{sourceError}</MessageBar>}
                {sourceChanged && <MessageBar intent="warning">The model source changed or lacks a pinned identity. Confirm its current configuration before registering.</MessageBar>}
                {sourceChanged && sourcesCurrent && !sourceError && selectedSource && eligibility(selectedSource) === null
                  && <Button className={styles.presetAction} disabled={busy || harnessBlocked}
                    onClick={() => selectModelSource(source)}>Use updated model source</Button>}
                {harness === 'acp' && <Text>Custom ACP uses harness-managed model access. Target-backed BYOK requires a provider-configuration adapter.</Text>}
                {!source && !credential.trim() && <Text>No backend credentials will be forwarded. Configure references under Launch and authentication if your harness needs them; your login is not inherited.</Text>}
                {harnessBlocked && <MessageBar intent="error">This harness does not support the selected inference protocol.</MessageBar>}
                {source && <Text>Model requests go through the selected PyRIT target. Its provider key is not forwarded; the model connection uses a scoped relay credential. Separate tool credentials remain selected. Local execution is not a credential-isolation boundary.</Text>}
                {source && authentication.trim() && <MessageBar intent="error">Remove the ACP authentication method explicitly before using BYOK.</MessageBar>}
                {source && environment === 'docker' && <Text>Docker requires an explicitly reachable backend inference-relay address. No provider key is copied into the container.</Text>}
                {sourceBlocked && <MessageBar intent="error">Selected model source is unavailable or incompatible.</MessageBar>}
                <Field label="Harness-managed model ID" hint={source ? 'Optional base model identity for harness behavior. The provider deployment remains fixed by the selected target.' : 'Blank uses the harness default. An explicit ID is validated by the harness on startup.'}>
                  <Input value={model} onChange={(_, data) => setModel(data.value)} />
                </Field>
              </section>
              <section className={styles.section} aria-label="Environment template">
                <Text weight="semibold">Environment template</Text>
                <Field label="Registered environment template">
                  <Select className={styles.select} value={environmentPreset?.name ?? ''}
                    disabled={busy || catalogUnavailable} onChange={(_, data) => selectEnvironmentPreset(data.value)}>
                    <option value="">Custom configuration</option>
                    {environmentPreset && !catalog?.environment_templates.some((item) => item.name === environmentPreset.name)
                      && <option value={environmentPreset.name}>{environmentPreset.name} (unavailable)</option>}
                    {catalog?.environment_templates.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                  </Select>
                </Field>
                {environmentPreset ? <>
                  <Text size={200}>Registered by {environmentPreset.initializer ?? 'Python registration'}. Local execution still requires your acknowledgement.</Text>
                  {environmentChanged && !catalogUnavailable && catalog?.environment_templates.some((item) => item.name === environmentPreset.name)
                    && <Button className={styles.presetAction} disabled={busy} onClick={() => selectEnvironmentPreset(environmentPreset.name)}>Use updated template</Button>}
                  <Button className={styles.presetAction} disabled={busy} onClick={() => setEnvironmentPreset(null)}>Customize template</Button>
                </> : <Text size={200}>{catalog?.environment_templates.length === 0 ? 'No registered templates. Configure an environment_template initializer or enter custom settings.' : 'Custom settings for this target.'}</Text>}
                <fieldset className={styles.settingsFields} disabled={busy || Boolean(environmentPreset)}>
                <Field label="Execution environment">
                  <Select className={styles.select} value={environment} onChange={(_, data) => setEnvironment(data.value === 'local' ? 'local' : 'docker')}>
                    <option value="docker">Docker</option><option value="local">Local - not sandboxed</option>
                  </Select>
                </Field>
                {environment === 'docker' && <Field label="Docker image" required>
                  <Input value={image} onChange={(_, data) => setImage(data.value)} required />
                </Field>}
                {environment === 'docker' && <Field label="Starting workspace">
                  <Select className={styles.select} value={workspaceBase} onChange={(_, data) => setWorkspaceBase(data.value === 'empty' ? 'empty' : 'image')}>
                    <option value="image">Image-provided workspace</option>
                    <option value="empty">Empty private workspace</option>
                  </Select>
                </Field>}
                <Text size={200}>{environment === 'local' ? 'A fresh private workspace is created for each conversation.' : 'The image must contain the selected harness executable. No host repository or home is mounted.'}</Text>
                <details>
                  <summary>Workspace initialization</summary>
                  <Field label="Working directory" hint="Relative to the execution workspace. Artifacts remain relative to the workspace root.">
                    <Input value={workingDirectory} onChange={(_, data) => setWorkingDirectory(data.value)} />
                  </Field>
                  <Field label="Import directory (optional)" hint="Advanced: a private copy of this backend directory overlays the starting workspace. Not required for image-provided content.">
                    <Input value={fixture} onChange={(_, data) => setFixture(data.value)} />
                  </Field>
                </details>
                {environment === 'docker' && <Text size={200}>Network: {environmentBase?.docker_network ?? 'bridge'} · Memory: {environmentBase?.docker_memory ?? '1g'} · CPUs: {environmentBase?.docker_cpus ?? 1}</Text>}
                </fieldset>
                {environment === 'local' && <Checkbox label="I understand local tools can access the host"
                  checked={acknowledged} onChange={(_, data) => setAcknowledged(data.checked === true)} />}
              </section>
              </div>
              <details>
                <summary>Execution limits and retained evidence</summary>
                <div className={styles.section}>
                  <Field label="Turn deadline (seconds)"><Input type="number" min={1} max={3600} value={turnTimeout} onChange={(_, data) => setTurnTimeout(data.value)} required /></Field>
                  <Field label="Idle expiry (seconds)"><Input type="number" min={1} max={86400} value={idleTimeout} onChange={(_, data) => setIdleTimeout(data.value)} required /></Field>
                  <Field label="Maximum lifetime (seconds)"><Input type="number" min={1} max={86400} value={lifetime} onChange={(_, data) => setLifetime(data.value)} required /></Field>
                  <Field label="Artifacts to retain" hint="Comma-separated workspace-relative files, e.g. receipt.json, orders.json.">
                    <Input value={artifacts} onChange={(_, data) => setArtifacts(data.value)} />
                  </Field>
                  <Text>Standard ACP event capture is always enabled. Native harness diagnostics are not configured here.</Text>
                  {source && <>
                    <Field label="Maximum inference requests per execution"><Input type="number" min={1} max={100} value={maxInference} onChange={(_, data) => setMaxInference(data.value)} /></Field>
                    <Checkbox label="Retain inference request and response content (sensitive)" checked={captureInference}
                      onChange={(_, data) => setCaptureInference(data.checked === true)} />
                  </>}
                </div>
              </details>
            </DialogContent>
            <DialogActions>
              {onBack && <Button onClick={onBack} disabled={busy}>Back to target types</Button>}
              <Button onClick={onClose} disabled={busy}>Cancel</Button>
              <Button type="submit" appearance="primary" disabled={busy || !ready || presetsBlocked || sourceBlocked || harnessBlocked || (Boolean(source) && Boolean(authentication.trim())) || !command.trim()
                || !name.trim() || (environment === 'docker' && !image.trim()) || (environment === 'local' && !acknowledged)}>
                {busy ? 'Registering...' : selectForChat ? 'Register and select for chat' : 'Register target'}
              </Button>
            </DialogActions>
          </form>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
