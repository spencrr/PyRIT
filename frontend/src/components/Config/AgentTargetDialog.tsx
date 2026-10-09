import { useEffect, useState } from 'react'

import {
  Button, Checkbox, Dialog, DialogActions, DialogBody, DialogContent, DialogSurface,
  DialogTitle, Field, Input, MessageBar, MessageBarBody, Select, Text,
} from '@fluentui/react-components'

import { targetsApi } from '@/services/api'
import { listRegisteredTargets } from '@/services/targetRegistry'
import { toApiError } from '@/services/errors'
import type { AgentTargetConfiguration, TargetInstance } from '@/types'

import { useAgentTargetDialogStyles } from './AgentTargetDialog.styles'

interface AgentTargetDialogProps {
  readonly onClose: () => void
  readonly onCreated: (target: TargetInstance) => void
  readonly initialConfiguration?: AgentTargetConfiguration
  readonly selectForChat?: boolean
}

export default function AgentTargetDialog({
  onClose, onCreated, initialConfiguration, selectForChat = false,
}: AgentTargetDialogProps) {
  const styles = useAgentTargetDialogStyles()
  const [name, setName] = useState(initialConfiguration ? `${initialConfiguration.name}-copy` : '')
  const [model, setModel] = useState(initialConfiguration?.model_binding.model ?? '')
  const [source, setSource] = useState(initialConfiguration?.model_binding.target_registry_name ?? '')
  const [wireApi, setWireApi] = useState<'completions' | 'responses'>(initialConfiguration?.model_binding.wire_api ?? 'completions')
  const [sources, setSources] = useState<TargetInstance[]>([])
  const [sourceError, setSourceError] = useState<string | null>(null)
  const [captureInference, setCaptureInference] = useState(initialConfiguration?.capture_inference_content ?? false)
  const [maxInference, setMaxInference] = useState(String(initialConfiguration?.max_inference_requests ?? 100))
  const [environment, setEnvironment] = useState<'docker' | 'local'>(
    initialConfiguration?.environment_template.environment ?? 'docker',
  )
  const [image, setImage] = useState(initialConfiguration?.environment_template.image ?? 'pyrit-copilot-receipt:1.0.93')
  const [fixture, setFixture] = useState(initialConfiguration?.environment_template.fixture_directory ?? '')
  const [credential, setCredential] = useState(
    initialConfiguration?.harness_profile.credential_env.join(', ') ?? 'COPILOT_GITHUB_TOKEN',
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
    let stopped = false
    listRegisteredTargets().then((targets: TargetInstance[]) => {
      if (!stopped) setSources(targets)
    }).catch((cause: unknown) => {
      if (!stopped) setSourceError(toApiError(cause).detail)
    })
    return () => { stopped = true }
  }, [])

  const eligibility = (target: TargetInstance): string | null => {
    const capabilities = target.inference_capabilities
    if (!capabilities) return 'No inference implementation'
    if (capabilities.blocked_reason) return capabilities.blocked_reason
    if (!capabilities.wire_apis.includes(wireApi)) return `Does not support ${wireApi}`
    if (!capabilities.streaming || !capabilities.tool_calls) return 'Streaming and tool passthrough required'
    const required = initialConfiguration?.harness_profile.inference_requirements?.input_modalities ?? ['text']
    if (required.some((modality: string) => !capabilities.input_modalities.includes(modality))) return 'Missing required input modality'
    return null
  }
  const selectedSource = sources.find((target: TargetInstance) => target.target_registry_name === source)
  const sourceBlocked = Boolean(source) && (!selectedSource || eligibility(selectedSource) !== null)

  const submit = async (): Promise<void> => {
    setBusy(true)
    setError(null)
    try {
      const configuration: AgentTargetConfiguration = {
        ...initialConfiguration,
        name: name.trim(),
        model_binding: source ? {
          model: model.trim(), target_registry_name: source,
          target_identifier_hash: selectedSource?.identifier.hash, wire_api: wireApi,
        } : { model: model.trim() },
        harness_profile: {
          command: initialConfiguration?.harness_profile.command ?? ['copilot', '--acp', '--stdio'],
          authentication_method: source ? null : initialConfiguration ? initialConfiguration.harness_profile.authentication_method : 'copilot-login',
          credential_env: source ? [] : credential.split(',').map((value: string) => value.trim()).filter(Boolean),
          permission_policy: permissionPolicy,
          inference_requirements: {
            ...initialConfiguration?.harness_profile.inference_requirements,
            wire_api: wireApi, streaming: true, tool_calls: true,
            input_modalities: initialConfiguration?.harness_profile.inference_requirements?.input_modalities ?? ['text'],
          },
        },
        environment_template: {
          ...initialConfiguration?.environment_template,
          environment, image: environment === 'docker' ? image.trim() : null,
          fixture_directory: fixture.trim() || null,
          expected_fixture_sha256: fixture === (initialConfiguration?.environment_template.fixture_directory ?? '')
            ? initialConfiguration?.environment_template.expected_fixture_sha256 : null,
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
          <DialogTitle>Configure agent target</DialogTitle>
          <form className={styles.form} onSubmit={(event) => { event.preventDefault(); void submit() }}>
            <DialogContent className={styles.content}>
              {error && <MessageBar intent="error"><MessageBarBody>{error}</MessageBarBody></MessageBar>}
              <Text>No resources are provisioned until the first send. This target is reusable in the current backend runtime.</Text>
              <Field label="Target name" required>
                <Input value={name} onChange={(_, data) => setName(data.value)} required />
              </Field>
              <section className={styles.section} aria-label="Harness profile">
                <Text weight="semibold">Harness profile</Text>
                <Text>{initialConfiguration?.harness_profile.command[0] ?? 'Copilot CLI'} · ACP</Text>
                {!source && <Field label="Credential references" hint="Comma-separated environment-variable names on the backend, never token values.">
                  <Input value={credential} onChange={(_, data) => setCredential(data.value)} />
                </Field>}
                <Field label="Tool permission policy">
                  <Select value={permissionPolicy} onChange={(_, data) => setPermissionPolicy(
                    data.value === 'allow_once' ? 'allow_once' : data.value === 'deny' ? 'deny' : 'ask',
                  )}>
                    <option value="ask">Ask operator (interactive chat)</option>
                    <option value="deny">Deny all requests (automation compatible)</option>
                    <option value="allow_once">Allow all requests once (automation compatible)</option>
                  </Select>
                </Field>
                <Text size={200}>Automated runs must select allow or deny. Approval is not a sandbox or a tool allowlist.</Text>
              </section>
              <section className={styles.section} aria-label="Model binding">
                <Text weight="semibold">Model binding</Text>
                <Field label="Inference protocol">
                  <Select value={wireApi} onChange={(_, data) => setWireApi(data.value === 'responses' ? 'responses' : 'completions')}>
                    <option value="completions">Chat Completions</option><option value="responses">Responses</option>
                  </Select>
                </Field>
                <Field label="Model source">
                  <Select value={source} onChange={(_, data) => setSource(data.value)}>
                    <option value="">Harness-managed access</option>
                    {sources.map((target: TargetInstance) => <option key={target.target_registry_name}
                      value={target.target_registry_name} disabled={eligibility(target) !== null}>
                      {target.target_registry_name}{eligibility(target) ? ` — ${eligibility(target)}` : ' — inference compatible'}
                    </option>)}
                  </Select>
                </Field>
                {sourceError && <MessageBar intent="warning">{sourceError}</MessageBar>}
                {source && <Text>Model requests go through the selected PyRIT target. Provider credentials stay in PyRIT; the execution receives only a scoped relay credential. Compatibility describes the adapter; verify the model's tool behavior.</Text>}
                {source && environment === 'docker' && <Text>Docker requires an explicitly reachable backend inference-relay address. No provider key is copied into the container.</Text>}
                {sourceBlocked && <MessageBar intent="error">Selected model source is unavailable or incompatible.</MessageBar>}
                <Field label="Harness-managed model ID" hint={source ? 'Optional base model identity for harness behavior. The provider deployment remains fixed by the selected target.' : 'Blank uses the harness default. An explicit ID is validated by the harness on startup.'}>
                  <Input value={model} onChange={(_, data) => setModel(data.value)} />
                </Field>
              </section>
              <section className={styles.section} aria-label="Environment template">
                <Text weight="semibold">Environment template</Text>
                <Field label="Execution environment">
                  <Select value={environment} onChange={(_, data) => setEnvironment(data.value === 'local' ? 'local' : 'docker')}>
                    <option value="docker">Docker</option><option value="local">Local - not sandboxed</option>
                  </Select>
                </Field>
                {environment === 'docker' && <Field label="Docker image" required>
                  <Input value={image} onChange={(_, data) => setImage(data.value)} required />
                </Field>}
                <Field label="Workspace fixture directory" hint="Optional backend-local directory copied fresh for each execution. The receipt Docker image already contains its fixture.">
                  <Input value={fixture} onChange={(_, data) => setFixture(data.value)} />
                </Field>
                {environment === 'local' && <Checkbox label="I understand local tools can access the host"
                  checked={acknowledged} onChange={(_, data) => setAcknowledged(data.checked === true)} />}
              </section>
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
              <Button onClick={onClose} disabled={busy}>Cancel</Button>
              <Button type="submit" appearance="primary" disabled={busy || sourceBlocked || !name.trim() || (environment === 'local' && !acknowledged)}>
                {busy ? 'Registering...' : selectForChat ? 'Register and select for chat' : 'Register target'}
              </Button>
            </DialogActions>
          </form>
        </DialogBody>
      </DialogSurface>
    </Dialog>
  )
}
