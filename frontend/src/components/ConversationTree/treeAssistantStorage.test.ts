import type {
  TreeAssistantAction, TreeAssistantCheckpoint, TreeAssistantContext, TreeAssistantProposal,
  TreeAssistantReceipt, TreeAssistantToolCall, TreeAssistantTurn, TreeNode, TreeWorkspace,
} from '@/types'

import { captureAssistantPrecondition, createAssistantContext } from './treeAssistant'
import { createTreeWorkspace, parseTreeWorkspace } from './treeModel'
import {
  deleteAssistantCheckpoint, exportAssistantChat, loadAssistantCheckpoint, parseAssistantCheckpoint, saveAssistantCheckpoint,
} from './treeAssistantStorage'

const WORKSPACE_ID = 'workspace'
const STORAGE_KEY = `pyrit:tree-assistant:v1:${WORKSPACE_ID}`
const PROPOSAL: TreeAssistantProposal = {
  id: 'proposal', workspace_id: WORKSPACE_ID, base_revision: 12, summary: 'Add a branch', status: 'pending',
  action: { kind: 'mutate', commands: [{ type: 'add', parentId: 'node', prompt: 'Compare', converters: [] }] },
}
const TOOL: TreeAssistantToolCall = {
  id: 'tool-call', name: 'inspect_tree', arguments: { node_id: 'node', include_scores: true },
  result: 'Recorded evidence', status: 'completed', duration_ms: 1.25, truncated: false,
}
const TURN: TreeAssistantTurn = {
  request_id: 'request', message: 'Explore the tree', reply: 'Review the proposed branch.', proposals: [PROPOSAL],
  tool_calls: [TOOL], usage: { input_tokens: 100, output_tokens: 20, total_tokens: 120 },
  context_summary: {
    workspace_id: WORKSPACE_ID, revision: 12, selected_node_id: 'node', node_count: 1,
    model: 'configured-model', api: 'Responses', instructions: 'Only propose changes.', tools: ['inspect_tree'], restored: false,
  },
}
const CONTEXT: TreeAssistantContext = {
  workspace_id: WORKSPACE_ID, revision: 12, name: 'Tree', objective: 'Evaluate',
  target_registry_name: 'target', target_identifier_hash: 'hash', selected_node_id: 'node',
  settings: { traversal: 'breadth-first', concurrency: 1, operation_budget: 20, scorer_ids: [] },
  nodes: [{
    id: 'node', parent_id: null, attempt_id: 'attempt', prompt: 'Saved prompt', converters: [],
    status: 'draft', pruned: false, kept: false, response_preview: 'Response', response_truncated: false, score_summary: '',
  }],
}
const RECEIPT: TreeAssistantReceipt = { status: 'applied', revision: 13, detail: 'Created one draft; no model calls.' }

function checkpoint(patch: Partial<TreeAssistantCheckpoint> = {}): TreeAssistantCheckpoint {
  return JSON.parse(JSON.stringify({
    schemaVersion: 1, revision: 0, workspaceId: WORKSPACE_ID, savedAt: '2026-09-01T12:00:00Z',
    session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns: [TURN] },
    archivedTurns: [], draft: 'Next question', pendingMessage: null, unreported: null, executing: null, ...patch,
  }))
}

function withTurn(turn: TreeAssistantTurn): TreeAssistantCheckpoint {
  return checkpoint({
    session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns: [turn] },
  })
}

function parse(value: unknown): TreeAssistantCheckpoint {
  return parseAssistantCheckpoint(JSON.stringify(value), WORKSPACE_ID)
}

function history(prefix: string, count: number): TreeAssistantTurn[] {
  return Array.from({ length: count }, (_value: unknown, index: number) => ({
    request_id: `${prefix}-${index}`, message: `Question ${index}`, reply: `Answer ${index}`, proposals: [],
  }))
}

function largeTree(): TreeWorkspace {
  const workspace = createTreeWorkspace({
    name: 'Large assistant tree', targetRegistryName: 'target', targetIdentifierHash: 'hash', labels: {}, systemPrompt: '',
  })
  workspace.id = WORKSPACE_ID
  workspace.revision = PROPOSAL.base_revision
  workspace.nodes = Array.from({ length: 300 }, (_value: unknown, index: number): TreeNode => ({
    id: `node-${index}`, attemptId: `attempt-${index}`, parentId: null, prompt: 'p'.repeat(100),
    converters: [], status: 'draft', pruned: false, kept: false,
  }))
  return parseTreeWorkspace(JSON.stringify(workspace))
}

describe('treeAssistantStorage', () => {
  beforeEach(() => {
    jest.clearAllMocks()
    localStorage.clear()
  })

  afterEach(() => { jest.restoreAllMocks() })

  it.each(['request', 'execution'] as const)('should save and export a valid 300-node host precondition in the %s journal', (kind) => {
    const workspace = largeTree()
    const context = createAssistantContext(workspace, null)
    const precondition = captureAssistantPrecondition(workspace)
    expect(precondition.semanticSignature.length).toBeGreaterThan(64_000)
    expect(new TextEncoder().encode(JSON.stringify(context)).length).toBeLessThan(512_000)
    const candidate = kind === 'request' ? checkpoint({
      pendingMessage: { request_id: 'large-request', message: 'Explore', context },
      preconditions: { 'large-request': precondition },
    }) : checkpoint({
      executing: { proposalId: PROPOSAL.id, baseRevision: workspace.revision, precondition },
      preconditions: { request: precondition },
    })
    const saved = saveAssistantCheckpoint(candidate)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
    expect(JSON.parse(exportAssistantChat(saved)).preconditions).toEqual(candidate.preconditions)
    if (kind === 'request') expect(saved.pendingMessage?.context.nodes).toHaveLength(300)
    else expect(saved.executing?.precondition).toEqual(precondition)
  })

  it('should export oversized retained host snapshots without weakening the aggregate storage limit', () => {
    const stored = saveAssistantCheckpoint(checkpoint())
    const precondition = captureAssistantPrecondition(largeTree())
    const turns: TreeAssistantTurn[] = Array.from({ length: 35 }, (_value: unknown, index: number) => ({
      request_id: `large-request-${index}`, message: 'Explore', reply: 'Review',
      proposals: [{ ...PROPOSAL, id: `large-proposal-${index}` }],
    }))
    const candidate = checkpoint({
      revision: stored.revision,
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'model', turns },
      preconditions: Object.fromEntries(turns.map((turn: TreeAssistantTurn) => [turn.request_id, precondition])),
    })
    const bytes = new TextEncoder().encode(JSON.stringify(candidate)).length
    expect(bytes).toBeGreaterThan(2 * 1024 * 1024)
    expect(bytes).toBeLessThan(16 * 1024 * 1024)
    expect(() => saveAssistantCheckpoint(candidate)).toThrow(/2 MB UTF-8 limit/)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(stored)
    const exported = JSON.parse(exportAssistantChat(candidate))
    expect(exported.preconditions).toEqual(candidate.preconditions)
    expect(exported.session.turns).toHaveLength(35)
    expect(exported.session).not.toHaveProperty('session_id')
  })

  it('should retain scalar limits outside the exact host semantic-signature fields', () => {
    const invalid = withTurn({ ...TURN, reply: 'r'.repeat(64_001) })
    expect(() => parse(invalid)).toThrow(/Checkpoint text.*64000/)
    expect(() => exportAssistantChat(invalid)).toThrow(/Checkpoint text.*64000/)
    const oversized = checkpoint({ preconditions: {
      request: { workspaceId: WORKSPACE_ID, baseRevision: 12, semanticSignature: 's'.repeat(16 * 1024 * 1024 + 1) },
    } })
    expect(() => exportAssistantChat(oversized)).toThrow(/Host semantic signature.*16777216|16 MB UTF-8 limit/)
  })

  it('should preserve host preconditions and interrupted execution across reload and export without changing the wire transcript', () => {
    const precondition = { workspaceId: WORKSPACE_ID, baseRevision: 12, semanticSignature: '{"complete":"host semantic snapshot"}' }
    const saved = saveAssistantCheckpoint(checkpoint({
      preconditions: { request: precondition },
      executing: { proposalId: PROPOSAL.id, baseRevision: 15, precondition },
    }))
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.preconditions?.request).toEqual(precondition)
    expect(Object.isFrozen(saved.preconditions?.request)).toBe(true)
    expect(saved.session?.turns[0]).toEqual(TURN)
    const exported = JSON.parse(exportAssistantChat(saved))
    expect(exported.executing.precondition).toEqual(precondition)
    expect(exported.preconditions.request).toEqual(precondition)
    expect(exported.session).not.toHaveProperty('session_id')
  })

  it('should continue to read revision-bound legacy checkpoints with no host precondition', () => {
    const legacy = parse(checkpoint({ executing: { proposalId: PROPOSAL.id, baseRevision: 12 } }))
    expect(legacy).not.toHaveProperty('preconditions')
    expect(legacy.executing).not.toHaveProperty('precondition')
  })

  it.each([
    { request: { workspaceId: 'another-workspace', baseRevision: 12, semanticSignature: 'snapshot' } },
    { request: { workspaceId: WORKSPACE_ID, baseRevision: 13, semanticSignature: 'snapshot' } },
    { unknownRequest: { workspaceId: WORKSPACE_ID, baseRevision: 12, semanticSignature: 'snapshot' } },
    { request: { workspaceId: WORKSPACE_ID, baseRevision: 12, semanticSignature: '' } },
  ])('should reject unbound or malformed host preconditions %#', (preconditions) => {
    expect(() => parse({ ...checkpoint(), preconditions })).toThrow(/precondition|signature/i)
  })

  it('should bind a pending request precondition to its original context revision', () => {
    const pendingMessage = { request_id: 'pending', message: 'Continue', context: CONTEXT }
    const preconditions = { pending: { workspaceId: WORKSPACE_ID, baseRevision: CONTEXT.revision, semanticSignature: 'full snapshot' } }
    expect(parse(checkpoint({ pendingMessage, preconditions })).preconditions).toEqual(preconditions)
    expect(() => parse(checkpoint({ pendingMessage, preconditions: { pending: { ...preconditions.pending, baseRevision: 99 } } }))).toThrow(/recorded request/)
  })

  it('should reject execution journals with a precondition from a different request revision', () => {
    expect(() => parse(checkpoint({ executing: {
      proposalId: PROPOSAL.id, baseRevision: 15,
      precondition: { workspaceId: WORKSPACE_ID, baseRevision: 15, semanticSignature: 'snapshot' },
    } }))).toThrow(/proposal revision/)
  })

  it('should round-trip a complete transcript synchronously without mutating the input', () => {
    const original = checkpoint()
    const saved = saveAssistantCheckpoint(original)
    expect(saved).not.toBeInstanceOf(Promise)
    expect(saved.revision).toBe(1)
    expect(saved.savedAt).not.toBe(original.savedAt)
    expect(original.revision).toBe(0)
    expect(saved.session).not.toBe(original.session)
    expect(Object.isFrozen(saved)).toBe(true)
    expect(Object.isFrozen(saved.session?.turns[0].tool_calls?.[0].arguments)).toBe(true)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '')).toEqual(saved)
    expect(saveAssistantCheckpoint({ ...saved, draft: 'Updated draft' }).revision).toBe(2)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.draft).toBe('Updated draft')
  })

  it('should return null only for a missing workspace-scoped record', () => {
    saveAssistantCheckpoint(checkpoint())
    expect(loadAssistantCheckpoint('another-workspace')).toBeNull()
    localStorage.setItem('pyrit:conversation-tree:v1:other', '{broken')
    expect(loadAssistantCheckpoint('other')).toBeNull()
    expect(() => loadAssistantCheckpoint('../workspace')).toThrow('workspace ID')
  })

  it('should preserve a draft before the server session exists', () => {
    const saved = saveAssistantCheckpoint(checkpoint({ session: null }))
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
  })

  it('should compare the latest revision on every save and leave the stale snapshot exportable', () => {
    const first = saveAssistantCheckpoint(checkpoint())
    const second = saveAssistantCheckpoint({ ...first, draft: 'Second writer' })
    const stale = { ...first, draft: 'Unsaved local draft' }
    expect(() => saveAssistantCheckpoint(stale)).toThrow(/revision conflict.*Export/i)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(second)
    expect(JSON.parse(exportAssistantChat(stale)).draft).toBe('Unsaved local draft')
    expect(stale.revision).toBe(1)
    expect(() => saveAssistantCheckpoint(checkpoint())).toThrow('revision conflict')
  })

  it('should CAS deletion and reject stale saves after deletion', () => {
    const first = saveAssistantCheckpoint(checkpoint())
    const second = saveAssistantCheckpoint(first)
    expect(() => deleteAssistantCheckpoint(WORKSPACE_ID, first.revision)).toThrow('revision conflict')
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(second)
    deleteAssistantCheckpoint(WORKSPACE_ID, second.revision)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toBeNull()
    expect(() => saveAssistantCheckpoint(second)).toThrow('deleted')
    expect(() => deleteAssistantCheckpoint(WORKSPACE_ID, second.revision)).toThrow('revision conflict')
    expect(() => deleteAssistantCheckpoint(WORKSPACE_ID, 0)).toThrow('revision conflict')
  })

  it.each(['QuotaExceededError', 'SecurityError'])('should report %s without consuming a revision or replacing stored data', (name: string) => {
    const saved = saveAssistantCheckpoint(checkpoint())
    const stored = localStorage.getItem(STORAGE_KEY)
    const write = jest.spyOn(Storage.prototype, 'setItem').mockImplementation((): void => { throw new DOMException('Storage failure', name) })
    expect(() => saveAssistantCheckpoint({ ...saved, draft: 'Retain this locally' })).toThrow(name)
    expect(localStorage.getItem(STORAGE_KEY)).toBe(stored)
    write.mockRestore()
    expect(saveAssistantCheckpoint(saved).revision).toBe(2)
  })

  it('should not replace inaccessible or malformed storage with an empty chat', () => {
    const read = jest.spyOn(Storage.prototype, 'getItem').mockImplementation((): string | null => {
      throw new DOMException('Access denied', 'SecurityError')
    })
    expect(() => loadAssistantCheckpoint(WORKSPACE_ID)).toThrow(/SecurityError.*Export/)
    expect(() => saveAssistantCheckpoint(checkpoint())).toThrow('SecurityError')
    read.mockRestore()
    localStorage.setItem(STORAGE_KEY, '{broken')
    expect(() => loadAssistantCheckpoint(WORKSPACE_ID)).toThrow(/malformed.*Nothing was changed/)
    expect(() => saveAssistantCheckpoint(checkpoint())).toThrow('malformed')
    expect(() => deleteAssistantCheckpoint(WORKSPACE_ID, 0)).toThrow('malformed')
    expect(localStorage.getItem(STORAGE_KEY)).toBe('{broken')
  })

  it('should preserve storage when a deletion fails', () => {
    const saved = saveAssistantCheckpoint(checkpoint())
    jest.spyOn(Storage.prototype, 'removeItem').mockImplementation((): void => { throw new DOMException('Access denied', 'SecurityError') })
    expect(() => deleteAssistantCheckpoint(WORKSPACE_ID, saved.revision)).toThrow('SecurityError')
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
  })

  it('should load first-version turns without newer optional trace fields', () => {
    const legacy = withTurn({ request_id: 'request', message: 'Question', reply: 'Answer', proposals: [PROPOSAL] })
    expect(parse(legacy)).toEqual(legacy)
    expect(parse(withTurn({ ...TURN, tool_calls: [], context_summary: null, usage: null })).session?.turns[0].tool_calls).toEqual([])
  })

  it('should normalize missing version-one archives without rewriting the saved record', () => {
    const legacy = checkpoint({ revision: 3 })
    delete legacy.archivedTurns
    const serialized = JSON.stringify(legacy)
    localStorage.setItem(STORAGE_KEY, serialized)
    const loaded = loadAssistantCheckpoint(WORKSPACE_ID)
    expect(loaded).toEqual({ ...legacy, archivedTurns: [] })
    expect(Object.isFrozen(loaded?.archivedTurns)).toBe(true)
    expect(localStorage.getItem(STORAGE_KEY)).toBe(serialized)
    expect(legacy.archivedTurns).toBeUndefined()
    expect(JSON.parse(exportAssistantChat(legacy)).archivedTurns).toEqual([])
    expect(saveAssistantCheckpoint(legacy).archivedTurns).toEqual([])
  })

  it('should persist 1000 archived turns independently of 50 live turns', () => {
    const turns = history('live', 50)
    const archivedTurns = history('archived', 1000)
    const original = checkpoint({
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns },
      archivedTurns,
    })
    const saved = saveAssistantCheckpoint(original)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
    expect(saved.session?.turns).toEqual(turns)
    expect(saved.archivedTurns).toEqual(archivedTurns)
    expect(Object.isFrozen(saved.archivedTurns?.[0])).toBe(true)
    expect(() => parse({
      ...saved, session: { ...saved.session, turns: [...turns, ...history('extra-live', 1)] },
    })).toThrow('Session turns must contain 0 to 50 items')
    expect(() => exportAssistantChat(checkpoint({
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns: history('live', 51) },
      archivedTurns,
    }))).toThrow('Session turns must contain 0 to 50 items')
  })

  it('should retain archives when no live session exists', () => {
    const saved = saveAssistantCheckpoint(checkpoint({ session: null, archivedTurns: history('archived', 2) }))
    expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
    const exported = JSON.parse(exportAssistantChat(saved))
    expect(exported.session).toBeNull()
    expect(exported.archivedTurns).toEqual(saved.archivedTurns)
  })

  it('should validate archived turns exactly like live turns, including workspace and receipt consistency', () => {
    const invalid = [
      null,
      {},
      [null],
      [{ ...TURN, message: 'x'.repeat(32_001) }],
      [{ ...TURN, unknown: true }],
      [{ ...TURN, proposals: [{ ...PROPOSAL, workspace_id: 'other' }] }],
      [{ ...TURN, context_summary: { ...TURN.context_summary, workspace_id: 'other' } }],
      [{ ...TURN, proposals: [{ ...PROPOSAL, status: 'applied' }] }],
      [{ ...TURN, tool_calls: [{ ...TOOL, arguments: { api_key: 'not-redacted' } }] }],
    ]
    for (const archivedTurns of invalid) {
      expect(() => parse({ ...checkpoint({ session: null }), archivedTurns })).toThrow('malformed')
    }
  })

  it('should share request and proposal ID uniqueness across archived, live and pending turns', () => {
    const archived: TreeAssistantTurn = { ...TURN, request_id: 'archived', proposals: [{ ...PROPOSAL, id: 'archived-proposal' }] }
    const invalid: TreeAssistantTurn[][] = [
      [{ ...archived, request_id: TURN.request_id }],
      [{ ...archived, request_id: PROPOSAL.id }],
      [{ ...archived, proposals: [PROPOSAL] }],
      [archived, archived],
      [archived, { ...archived, request_id: 'other-archived' }],
    ]
    for (const archivedTurns of invalid) {
      const original = checkpoint({ archivedTurns })
      expect(() => parse(original)).toThrow('unique')
      expect(() => exportAssistantChat(original)).toThrow('unique')
    }
    expect(() => parse(checkpoint({
      archivedTurns: [archived], pendingMessage: { request_id: 'archived', message: 'Next', context: CONTEXT },
    }))).toThrow('unique')
  })

  it('should export archived evidence and uncertain receipts without the live session capability', () => {
    const receipt: TreeAssistantReceipt = { status: 'failed', revision: 13, detail: 'Interrupted; outcome may be partial or unknown.' }
    const proposal: TreeAssistantProposal = { ...PROPOSAL, id: 'archived-proposal', status: receipt.status, result: receipt }
    const archived: TreeAssistantTurn = {
      ...TURN, request_id: 'archived', proposals: [proposal],
      tool_calls: [{ ...TOOL, result: 'Evidence with private-server-capability' }],
    }
    const saved = saveAssistantCheckpoint(checkpoint({
      archivedTurns: [archived], unreported: { proposalId: proposal.id, receipt, error: 'Acknowledgement unavailable' },
    }))
    const write = jest.spyOn(Storage.prototype, 'setItem')
    const exported = exportAssistantChat(saved)
    const bundle = JSON.parse(exported)
    expect(bundle.archivedTurns).toEqual([{
      ...archived, tool_calls: [{ ...TOOL, result: 'Evidence with [session capability omitted]' }],
    }])
    expect(bundle.unreported).toEqual(saved.unreported)
    expect(bundle.session.turns).toEqual(saved.session?.turns)
    expect(exported).not.toContain('private-server-capability')
    expect(exported).not.toContain('session_id')
    expect(write).not.toHaveBeenCalled()
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.archivedTurns?.[0].tool_calls?.[0].result).toBe(archived.tool_calls?.[0].result)
  })

  it('should export up to 50 additional archived turns after the persistence cap without altering storage', () => {
    const saved = saveAssistantCheckpoint(checkpoint({
      archivedTurns: history('archived', 1000),
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns: history('live', 50) },
    }))
    const stored = localStorage.getItem(STORAGE_KEY)
    const retained = { ...saved, archivedTurns: history('archived', 1050), draft: 'Retain this unsaved draft.' }
    const write = jest.spyOn(Storage.prototype, 'setItem')
    expect(() => saveAssistantCheckpoint(retained)).toThrow('Archived turns must contain 0 to 1000 items')
    expect(() => parse(retained)).toThrow('Archived turns must contain 0 to 1000 items')
    const bundle = JSON.parse(exportAssistantChat(retained))
    expect(bundle.archivedTurns).toEqual(retained.archivedTurns)
    expect(bundle.session.turns).toEqual(retained.session?.turns)
    expect(bundle.draft).toBe(retained.draft)
    expect(bundle.revision).toBe(saved.revision)
    expect(bundle.session.session_id).toBeUndefined()
    expect(localStorage.getItem(STORAGE_KEY)).toBe(stored)
    expect(write).not.toHaveBeenCalled()
    expect(() => exportAssistantChat({ ...retained, archivedTurns: history('archived', 1051) })).toThrow('Archived turns must contain 0 to 1050 items')
    const malformedExtra = { ...history('extra', 1)[0], message: 'x'.repeat(32_001) }
    expect(() => exportAssistantChat({
      ...saved, archivedTurns: [...history('archived', 1000), malformedExtra],
    })).toThrow('32000 characters')
  })

  it('should retain the shared 2 MiB storage ceiling while permitting larger archived exports', () => {
    const archivedTurns = history('archived', 40).map((turn: TreeAssistantTurn) => ({ ...turn, reply: '🌲'.repeat(15_000) }))
    const original = checkpoint({ archivedTurns })
    expect(() => saveAssistantCheckpoint(original)).toThrow('2 MB UTF-8')
    expect(() => parse(original)).toThrow('2 MB UTF-8')
    expect(JSON.parse(exportAssistantChat(original)).archivedTurns).toEqual(archivedTurns)
    expect(localStorage.length).toBe(0)
  })

  it('should reject unsupported versions, malformed fields and workspace mismatches', () => {
    const original = checkpoint()
    const invalid = [
      { ...original, schemaVersion: 2 },
      { ...original, schemaVersion: '1' },
      { ...original, workspaceId: 'other' },
      { ...original, revision: -1 },
      { ...original, revision: 1.5 },
      { ...original, savedAt: 'not a date' },
      { ...original, draft: 4 },
      { ...original, unknown: true },
      { ...original, session: { ...original.session, workspace_id: 'other' } },
      withTurn({ ...TURN, proposals: [{ ...PROPOSAL, workspace_id: 'other' }] }),
      withTurn({ ...TURN, context_summary: { ...TURN.context_summary, workspace_id: 'other' } } as TreeAssistantTurn),
      { ...original, pendingMessage: { request_id: 'pending', message: 'Next', context: { ...CONTEXT, workspace_id: 'other' } } },
      { ...original, session: null, pendingMessage: { request_id: 'pending', message: 'Next', context: CONTEXT } },
    ]
    for (const value of invalid) expect(() => parse(value)).toThrow('malformed')
    expect(() => parseAssistantCheckpoint('null', WORKSPACE_ID)).toThrow('malformed')
    expect(() => parseAssistantCheckpoint('[]', WORKSPACE_ID)).toThrow('malformed')
  })

  it('should enforce globally unique request and proposal IDs', () => {
    const original = checkpoint()
    expect(() => parse({ ...original, session: { ...original.session, turns: [TURN, TURN] } })).toThrow('unique')
    expect(() => parse(withTurn({ ...TURN, proposals: [{ ...PROPOSAL, id: TURN.request_id }] }))).toThrow('unique')
    expect(() => parse({ ...original, pendingMessage: { request_id: TURN.request_id, message: 'Again', context: CONTEXT } })).toThrow('unique')
    expect(() => parse(withTurn({ ...TURN, tool_calls: [TOOL, TOOL] }))).toThrow('unique')
  })

  it('should preserve immutable historical pending context without adding live autonomy permission', () => {
    const context = { ...CONTEXT, autonomy: { root_node_id: 'node', remaining_operations: 4, remaining_turns: 3, goal: 'Continue' } }
    const original = checkpoint({ pendingMessage: { request_id: 'pending', message: 'Next', context } })
    const saved = saveAssistantCheckpoint(original)
    expect(original.pendingMessage?.context.autonomy).toEqual(context.autonomy)
    expect(saved.pendingMessage?.context.autonomy).toEqual(context.autonomy)
    expect(Object.isFrozen(saved.pendingMessage?.context.autonomy)).toBe(true)
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY) ?? '').autonomy).toBeUndefined()
    const pending = saved.pendingMessage
    expect(pending).not.toBe(original.pendingMessage)
    expect(Object.isFrozen(pending?.context.nodes[0])).toBe(true)
    if (!pending) throw new Error('Missing pending context')
    expect(() => { pending.context.nodes[0].prompt = 'Changed' }).toThrow(TypeError)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.pendingMessage?.context.nodes[0].prompt).toBe('Saved prompt')
    expect(parse(original)).toEqual(original)
    expect(JSON.parse(exportAssistantChat(saved)).pendingMessage.context.autonomy).toEqual(context.autonomy)
    expect(() => parse({ ...original, autonomy: context.autonomy })).toThrow('unsupported fields')
  })

  it('should validate context node references, settings and bounded data', () => {
    const invalidContexts = [
      { ...CONTEXT, revision: -1 },
      { ...CONTEXT, selected_node_id: 'missing' },
      { ...CONTEXT, nodes: [CONTEXT.nodes[0], CONTEXT.nodes[0]] },
      { ...CONTEXT, nodes: [{ ...CONTEXT.nodes[0], parent_id: 'missing' }] },
      { ...CONTEXT, nodes: [{ ...CONTEXT.nodes[0], parent_id: 'node' }] },
      { ...CONTEXT, nodes: [{ ...CONTEXT.nodes[0], status: 'arbitrary' }] },
      { ...CONTEXT, nodes: [{ ...CONTEXT.nodes[0], response_preview: 'x'.repeat(2001) }] },
      { ...CONTEXT, settings: { ...CONTEXT.settings, concurrency: 3 } },
      { ...CONTEXT, settings: { ...CONTEXT.settings, operation_budget: 0 } },
      { ...CONTEXT, autonomy: { root_node_id: 'missing', remaining_operations: 4, remaining_turns: 3, goal: 'Continue' } },
      { ...CONTEXT, autonomy: { root_node_id: 'node', remaining_operations: -1, remaining_turns: 3, goal: 'Continue' } },
      { ...CONTEXT, autonomy: { root_node_id: 'node', remaining_operations: 4, remaining_turns: 51, goal: 'Continue' } },
      { ...CONTEXT, autonomy: { root_node_id: 'node', remaining_operations: 4, remaining_turns: 3, goal: 'Continue', autoRun: true } },
      { ...CONTEXT, nodes: Array.from({ length: 301 }, (_value: unknown, index: number) => ({ ...CONTEXT.nodes[0], id: `node-${index}` })) },
      { ...CONTEXT, nodes: Array.from({ length: 20 }, (_value: unknown, index: number) => ({
        ...CONTEXT.nodes[0], id: index === 0 ? 'node' : `node-${index}`, prompt: 'x'.repeat(32_000),
      })) },
    ]
    for (const context of invalidContexts) {
      expect(() => parse({ ...checkpoint(), pendingMessage: { request_id: 'pending', message: 'Next', context } })).toThrow('malformed')
    }
  })

  it('should accept all supported action forms without executing them', () => {
    const actions: TreeAssistantAction[] = [
      { kind: 'mutate', commands: [
        { type: 'add', parentId: null, prompt: 'Root' },
        { type: 'edit', nodeId: 'node', prompt: 'Edit', converters: [] },
        { type: 'fork', nodeId: 'node', prompt: 'Fork', converters: [{ type: 'StringJoinConverter', params: { join_value: '-' } }] },
        { type: 'childVariants', nodeId: 'node', variants: [{ prompt: 'Variant', converters: [] }] },
        { type: 'sample', nodeId: 'node', count: 2 },
        { type: 'retry', nodeId: 'node', scope: 'subtree' },
        { type: 'prune', nodeId: 'node', pruned: true },
        { type: 'keep', nodeId: 'node' },
      ] },
      { kind: 'run', node_ids: ['node'] },
      { kind: 'score', node_ids: ['node'] },
      { kind: 'plan', run: true, steps: [
        { id: 'first', parent: null, prompt: 'Root', converters: [] },
        { id: 'second', parent: { step_id: 'first' }, prompt: 'Child', converters: [] },
        { id: 'third', parent: { node_id: 'node' }, prompt: 'Existing parent', converters: [] },
      ] },
    ]
    for (const action of actions) {
      expect(parse(withTurn({ ...TURN, proposals: [{ ...PROPOSAL, action }] })).session?.turns[0].proposals[0].action).toEqual(action)
    }
    expect(localStorage.length).toBe(0)
  })

  it('should reject invalid commands, plan ordering and unknown action fields', () => {
    const actions = [
      { kind: 'settings', commands: [] },
      { kind: 'run', node_ids: [] },
      { kind: 'run', node_ids: ['node', 'node'] },
      { kind: 'run', node_ids: ['node'], autoApprove: true },
      { kind: 'mutate', commands: [{ type: 'resize', nodeId: 'node' }] },
      { kind: 'mutate', commands: [{ type: 'sample', nodeId: 'node', count: 21 }] },
      { kind: 'mutate', commands: [{ type: 'prune', nodeId: 'node', pruned: 'true' }] },
      { kind: 'mutate', commands: [{ type: 'retry', nodeId: 'node', scope: 'all' }] },
      { kind: 'mutate', commands: [{ type: 'childVariants', nodeId: 'node', variants: [] }] },
      { kind: 'mutate', commands: [{ type: 'edit', nodeId: 'node', prompt: 'Edit' }] },
      { kind: 'mutate', commands: [{ type: 'add', parentId: null, prompt: 'Root', converters: [{ type: 'Converter', params: [], unexpected: true }] }] },
      { kind: 'plan', run: false, steps: [{ id: 'step', parent: { step_id: 'step' }, prompt: 'Self', converters: [] }] },
      { kind: 'plan', run: true, steps: [
        { id: 'step', parent: null, prompt: 'First', converters: [] },
        { id: 'step', parent: null, prompt: 'Duplicate', converters: [] },
      ] },
    ]
    for (const action of actions) {
      expect(() => parse({ ...checkpoint(), session: { ...checkpoint().session, turns: [{ ...TURN, proposals: [{ ...PROPOSAL, action }] }] } })).toThrow('malformed')
    }
  })

  it('should require proposal status and unreported receipts to agree', () => {
    const resolved = { ...PROPOSAL, status: RECEIPT.status, result: RECEIPT }
    const valid = { ...withTurn({ ...TURN, proposals: [resolved] }), unreported: { proposalId: PROPOSAL.id, receipt: RECEIPT, error: 'Connection lost' } }
    expect(parse(valid)).toEqual(valid)
    for (const invalid of [
      withTurn({ ...TURN, proposals: [{ ...PROPOSAL, result: RECEIPT }] }),
      withTurn({ ...TURN, proposals: [{ ...PROPOSAL, status: 'applied' }] }),
      withTurn({ ...TURN, proposals: [{ ...resolved, result: { ...RECEIPT, status: 'failed' } }] }),
      withTurn({ ...TURN, proposals: [{ ...resolved, result: { ...RECEIPT, revision: 11 } }] }),
      { ...valid, unreported: { ...valid.unreported, proposalId: 'unknown' } },
      { ...valid, unreported: { ...valid.unreported, receipt: { ...RECEIPT, detail: 'Different result' } } },
      { ...valid, executing: { proposalId: PROPOSAL.id, baseRevision: 12 } },
    ]) expect(() => parse(invalid)).toThrow('malformed')
  })

  it('should restore an executing marker as a journal only, not change the pending proposal', () => {
    const executing = { proposalId: PROPOSAL.id, baseRevision: 12 }
    const saved = saveAssistantCheckpoint(checkpoint({ executing }))
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.executing).toEqual(executing)
    expect(loadAssistantCheckpoint(WORKSPACE_ID)?.session?.turns[0].proposals[0]).toEqual(PROPOSAL)
    expect(JSON.parse(exportAssistantChat(saved)).executing).toEqual(executing)
    expect(() => parse(checkpoint({ executing: { ...executing, proposalId: 'missing' } }))).toThrow('existing pending proposal')
    expect(() => parse(checkpoint({ executing: { ...executing, baseRevision: 11 } }))).toThrow('consistent revision')
    expect(() => parse({
      ...withTurn({ ...TURN, proposals: [{ ...PROPOSAL, status: RECEIPT.status, result: RECEIPT }] }), executing,
    })).toThrow('pending proposal')
  })

  it('should bound turns, UTF-8 bytes, trace text, metrics and JSON complexity', () => {
    const original = checkpoint()
    const turns = Array.from({ length: 51 }, (_value: unknown, index: number) => ({ ...TURN, request_id: `request-${index}`, proposals: [] }))
    expect(() => parse({ ...original, session: { ...original.session, turns } })).toThrow('50')
    const largeTurns = turns.slice(0, 40).map((turn: TreeAssistantTurn) => ({ ...turn, reply: '🌲'.repeat(15_000) }))
    const tooLarge = { ...original, session: { ...original.session, turns: largeTurns } }
    expect(JSON.stringify(tooLarge).length).toBeLessThan(2 * 1024 * 1024)
    expect(() => parse(tooLarge)).toThrow('2 MB UTF-8')
    expect(() => saveAssistantCheckpoint(tooLarge)).toThrow('2 MB UTF-8')
    expect(JSON.parse(exportAssistantChat(tooLarge)).session.turns).toEqual(largeTurns)
    expect(localStorage.length).toBe(0)
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, result: 'x'.repeat(64_001) }] }))).toThrow('malformed')
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, duration_ms: -1 }] }))).toThrow('duration')
    expect(() => parse(withTurn({ ...TURN, usage: { input_tokens: -1 } }))).toThrow('Token usage')
    let nested: Record<string, unknown> = {}
    for (let index = 0; index < 25; index += 1) nested = { child: nested }
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: nested }] }))).toThrow('deeply nested')
    expect(() => saveAssistantCheckpoint({ ...original, revision: Number.MAX_SAFE_INTEGER })).toThrow('revision')
  })

  it.each(['api_key', 'Authorization', 'client_secret', 'accessToken', 'headers', 'password', 'credentials', 'Cookie'])(
    'should refuse %s in tool arguments rather than persist or export credentials', (key: string) => {
      const unsafe = withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { nested: { [key]: 'placeholder-not-a-real-secret' } } }] })
      expect(() => parse(unsafe)).toThrow('Credentials')
      expect(() => saveAssistantCheckpoint(unsafe)).toThrow('Credentials')
      expect(() => exportAssistantChat(unsafe)).toThrow('Credentials')
      expect(localStorage.length).toBe(0)
    },
  )

  it.each(['Authorization: Bearer placeholder-value', '{"api_key":"placeholder-value"}', 'client_secret=placeholder-value'])(
    'should preserve credential discussion in recorded tool text without inferring secrets: %s', (result: string) => {
      const original = withTurn({ ...TURN, tool_calls: [{ ...TOOL, result }] })
      const saved = saveAssistantCheckpoint(original)
      expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
      expect(JSON.parse(exportAssistantChat(saved)).session.turns).toEqual(original.session?.turns)
    },
  )

  it.each(['api_key', 'Authorization', 'client_secret', 'accessToken', 'headers', 'password', 'credentials', 'Cookie'])(
    'should preserve the legacy uppercase redaction marker for %s only in tool arguments', (key: string) => {
      const original = withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { nested: [{ [key]: '[REDACTED]' }] } }] })
      expect(parse(original)).toEqual(original)
      const saved = saveAssistantCheckpoint(original)
      expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
      expect(JSON.parse(exportAssistantChat(saved)).session.turns).toEqual(original.session?.turns)
    },
  )

  it.each(['api_key', 'key', 'request_token', 'clientSecret', 'password', 'credential', 'authorization', 'headers', 'cookie'])(
    'should preserve the exact lowercase backend redaction marker for %s only in tool arguments', (key: string) => {
      const original = withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { nested: [{ [key]: '[redacted]' }] } }] })
      const saved = saveAssistantCheckpoint(original)
      expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
      expect(JSON.parse(exportAssistantChat(saved)).session.turns).toEqual(original.session?.turns)
      expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { [key]: 'not-redacted' } }] }))).toThrow('Credentials')
    },
  )

  it.each([null, false, 0, ['[REDACTED]'], { value: '[REDACTED]' }, '[Redacted]', '[REDACTED] plus other text'].map((value: unknown) => ({ value })))(
    'should reject a nonexact redaction value in credential-like tool argument fields: $value', ({ value }: { value: unknown }) => {
      const unsafe = withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { nested: [{ api_key: value }] } }] })
      expect(() => parse(unsafe)).toThrow('Credentials')
      expect(() => saveAssistantCheckpoint(unsafe)).toThrow('Credentials')
      expect(() => exportAssistantChat(unsafe)).toThrow('Credentials')
      expect(localStorage.length).toBe(0)
    },
  )

  it('should reject structured credential fields in plans and pending context even when redacted', () => {
    const converters = [{ type: 'Converter', params: { nested: [{ api_key: '[redacted]' }] } }]
    const plan = withTurn({ ...TURN, proposals: [{ ...PROPOSAL, action: {
      kind: 'plan', run: false, steps: [{ id: 'step', parent: null, prompt: 'Compare', converters }],
    } }] })
    const mutation = withTurn({ ...TURN, proposals: [{ ...PROPOSAL, action: {
      kind: 'mutate', commands: [{ type: 'add', parentId: null, prompt: 'Compare', converters }],
    } }] })
    const pending = checkpoint({ pendingMessage: {
      request_id: 'pending', message: 'Next',
      context: { ...CONTEXT, nodes: [{ ...CONTEXT.nodes[0], converters }] },
    } })
    for (const unsafe of [plan, mutation, pending]) {
      expect(() => parse(unsafe)).toThrow('Credentials')
      expect(() => saveAssistantCheckpoint(unsafe)).toThrow('Credentials')
      expect(() => exportAssistantChat(unsafe)).toThrow('Credentials')
    }
    expect(() => parse({ ...checkpoint(), Authorization: '[REDACTED]' })).toThrow('unsupported fields')
    expect(localStorage.length).toBe(0)
  })

  it.each(['api_key: placeholder', '{"Authorization":"[REDACTED]"}', 'api[_-]?key\\s*[:=]\\s*\\S+', '', 'password: ask the user'])(
    'should preserve free-form transcript and configuration text without inferring secrets: %j', (value: string) => {
      const original = {
        ...withTurn({
          ...TURN, message: value, reply: value,
          context_summary: TURN.context_summary ? { ...TURN.context_summary, instructions: value } : null,
          tool_calls: [{ ...TOOL, arguments: { prompt: value }, result: value }],
          proposals: [{ ...PROPOSAL, action: {
            kind: 'mutate', commands: [{
              type: 'add', parentId: null, prompt: value, converters: [{ type: 'Converter', params: { pattern: value } }],
            }],
          } }],
        }),
        draft: value,
        pendingMessage: {
          request_id: 'pending', message: 'Next',
          context: {
            ...CONTEXT, objective: value,
            nodes: [{ ...CONTEXT.nodes[0], prompt: value, response_preview: value, score_summary: value }],
          },
        },
      }
      expect(parse(original)).toEqual(original)
      const saved = saveAssistantCheckpoint(original)
      expect(loadAssistantCheckpoint(WORKSPACE_ID)).toEqual(saved)
      const exported = JSON.parse(exportAssistantChat(saved))
      expect(exported.session.turns).toEqual(original.session?.turns)
      expect(exported.draft).toBe(value)
      expect(exported.pendingMessage).toEqual(original.pendingMessage)
    },
  )

  it.each(['é', ',:'])('should enforce the 16000-byte tool argument limit with JSON separators and %j text', (fragment: string) => {
    const prompt = fragment.repeat(7993)
    expect(parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { prompt } }] })).session?.turns[0].tool_calls?.[0].arguments).toEqual({ prompt })
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: { prompt: `${prompt}x` } }] }))).toThrow('16000-byte')
  })

  it('should enforce result bytes, call counts and aggregate trace bounds', () => {
    const result = 'é'.repeat(12_000)
    expect(parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, result }] })).session?.turns[0].tool_calls?.[0].result).toBe(result)
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, result: `${result}x` }] }))).toThrow('24000-byte')
    expect(() => parse(withTurn({ ...TURN, tool_calls: [{ ...TOOL, result: 'x'.repeat(24_001) }] }))).toThrow('24000 characters')
    const calls = Array.from({ length: 17 }, (_value: unknown, index: number) => ({ ...TOOL, id: `call-${index}` }))
    expect(parse(withTurn({ ...TURN, tool_calls: calls.slice(0, 16) })).session?.turns[0].tool_calls).toHaveLength(16)
    expect(() => parse(withTurn({ ...TURN, tool_calls: calls }))).toThrow('16 items')
    const largeCalls = calls.slice(0, 6).map((tool: TreeAssistantToolCall) => ({ ...tool, result }))
    expect(parse(withTurn({ ...TURN, tool_calls: largeCalls.slice(0, 5) })).session?.turns[0].tool_calls).toHaveLength(5)
    expect(() => parse(withTurn({ ...TURN, tool_calls: largeCalls }))).toThrow('128000-byte')
  })

  it('should preserve bounded optional restoration notices and enforce context summary limits', () => {
    const summary = TURN.context_summary
    if (!summary) throw new Error('Missing test context summary')
    for (const notice of [null, 'x'.repeat(2000)]) {
      const context = { ...summary, restoration_notice: notice }
      const saved = saveAssistantCheckpoint({ ...withTurn({ ...TURN, context_summary: context }), revision: loadAssistantCheckpoint(WORKSPACE_ID)?.revision ?? 0 })
      expect(loadAssistantCheckpoint(WORKSPACE_ID)?.session?.turns[0].context_summary).toEqual(context)
      expect(JSON.parse(exportAssistantChat(saved)).session.turns[0].context_summary).toEqual(context)
    }
    expect(() => parse(withTurn({ ...TURN, context_summary: { ...summary, instructions: 'x'.repeat(32_001) } }))).toThrow('32000 characters')
    const tools = Array.from({ length: 17 }, (_value: unknown, index: number) => `tool-${index}`)
    expect(() => parse(withTurn({ ...TURN, context_summary: { ...summary, tools } }))).toThrow('16 items')
    const context = { ...summary, restoration_notice: 'x'.repeat(2001) }
    expect(() => parse(withTurn({ ...TURN, context_summary: context }))).toThrow('Restoration notice')
  })

  it('should omit wire-null token metrics rather than inventing values', () => {
    const original = checkpoint()
    const parsed = parse({ ...original, session: { ...original.session, turns: [
      { ...TURN, usage: { input_tokens: null, output_tokens: null, total_tokens: 0 } },
    ] } })
    expect(parsed.session?.turns[0].usage).toEqual({ total_tokens: 0 })
    expect(JSON.parse(exportAssistantChat(parsed)).session.turns[0].usage).toEqual({ total_tokens: 0 })
  })

  it('should reject unsafe prototypes, accessors, cycles and non-JSON data before writing', () => {
    const unsafeKey = JSON.stringify(checkpoint()).replace('"draft":"Next question"', '"draft":"Next question","__proto__":{"polluted":true}')
    expect(() => parseAssistantCheckpoint(unsafeKey, WORKSPACE_ID)).toThrow('unsafe object key')
    const getter = jest.fn((): string => 'Do not invoke')
    const object = Object.defineProperty(checkpoint(), 'draft', { enumerable: true, get: getter })
    expect(() => saveAssistantCheckpoint(object)).toThrow('non-JSON property')
    expect(getter).not.toHaveBeenCalled()
    const cyclic: Record<string, unknown> = {}
    cyclic.child = cyclic
    for (const argumentsValue of [{ date: new Date() }, { function: (): void => undefined }, { value: Infinity }, cyclic]) {
      expect(() => saveAssistantCheckpoint(
        { ...checkpoint(), session: { session_id: 'capability', workspace_id: WORKSPACE_ID, model: 'model',
          turns: [{ ...TURN, tool_calls: [{ ...TOOL, arguments: argumentsValue }] }] } })).toThrow('malformed')
    }
    expect(localStorage.length).toBe(0)
  })

  it('should export full portable evidence and pending status without server capabilities or storage writes', () => {
    const original = checkpoint({ pendingMessage: { request_id: 'pending', message: 'Next', context: CONTEXT } })
    const write = jest.spyOn(Storage.prototype, 'setItem')
    const exported = exportAssistantChat(original)
    const bundle = JSON.parse(exported)
    expect(bundle.schemaVersion).toBe(1)
    expect(bundle.exportedAt).toEqual(expect.any(String))
    expect(bundle.workspaceId).toBe(WORKSPACE_ID)
    expect(bundle.session.turns).toEqual(original.session?.turns)
    expect(bundle.pendingMessage).toEqual(original.pendingMessage)
    expect(bundle.draft).toBe(original.draft)
    expect(bundle.session.session_id).toBeUndefined()
    expect(exported).not.toContain('private-server-capability')
    expect(exported).not.toContain('session_id')
    expect(exported).toContain('\n  "schemaVersion": 1')
    expect(write).not.toHaveBeenCalled()
    expect(original.session?.session_id).toBe('private-server-capability')
    expect(() => parseAssistantCheckpoint(exported, WORKSPACE_ID)).toThrow('malformed')
    expect(exportAssistantChat(withTurn({ ...TURN, reply: 'private-server-capability' }))).not.toContain('private-server-capability')
  })

  it('should export a retained candidate when one valid incoming turn crosses storage capacity, including uncertain receipts', () => {
    const turns = Array.from({ length: 32 }, (_value: unknown, index: number): TreeAssistantTurn => ({
      ...TURN, request_id: `request-${index}`, message: 'm'.repeat(32_000), reply: 'r'.repeat(32_000), proposals: [],
    }))
    const original = checkpoint({
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns },
    })
    const session = original.session
    if (!session) throw new Error('Missing test session')
    const size = (value: unknown): number => new TextEncoder().encode(JSON.stringify(value)).length
    expect(size(original)).toBeLessThan(2 * 1024 * 1024)
    const saved = saveAssistantCheckpoint(original)
    const before = localStorage.getItem(STORAGE_KEY)
    const receipt: TreeAssistantReceipt = { status: 'failed', revision: 13, detail: 'Execution interrupted; outcome may be partial or unknown.' }
    const incoming: TreeAssistantTurn = {
      ...TURN, request_id: 'incoming', message: 'm'.repeat(32_000), reply: 'r'.repeat(32_000),
      proposals: [{ ...PROPOSAL, status: receipt.status, result: receipt }],
    }
    const retained: TreeAssistantCheckpoint = {
      ...saved, draft: 'Preserve this draft during recovery.',
      session: { ...session, turns: [...turns, incoming] },
      unreported: { proposalId: PROPOSAL.id, receipt, error: 'Acknowledgement unavailable' },
    }
    const retainedJson = JSON.stringify(retained)
    expect(size(retained)).toBeGreaterThan(2 * 1024 * 1024)
    const write = jest.spyOn(Storage.prototype, 'setItem')
    expect(() => saveAssistantCheckpoint(retained)).toThrow('2 MB UTF-8')
    expect(() => parse(retained)).toThrow('2 MB UTF-8')
    expect(localStorage.getItem(STORAGE_KEY)).toBe(before)

    const exported = exportAssistantChat(retained)
    const bundle = JSON.parse(exported)
    expect(new TextEncoder().encode(exported).length).toBeGreaterThan(2 * 1024 * 1024)
    expect(bundle.session.turns).toEqual(retained.session?.turns)
    expect(bundle.unreported).toEqual(retained.unreported)
    expect(bundle.draft).toBe(retained.draft)
    expect(bundle.revision).toBe(saved.revision)
    expect(bundle.savedAt).toBe(saved.savedAt)
    expect(exported).not.toContain('private-server-capability')
    expect(write).not.toHaveBeenCalled()
    expect(localStorage.getItem(STORAGE_KEY)).toBe(before)
    expect(JSON.stringify(retained)).toBe(retainedJson)
  })

  it('should keep export field, turn and depth bounds independent of the storage byte budget', () => {
    expect(() => exportAssistantChat(withTurn({ ...TURN, reply: 'x'.repeat(32_001) }))).toThrow('32000 characters')
    const turns = Array.from({ length: 51 }, (_value: unknown, index: number): TreeAssistantTurn => ({
      ...TURN, request_id: `request-${index}`, proposals: [],
    }))
    expect(() => exportAssistantChat(checkpoint({
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns },
    }))).toThrow('50 items')
    let nested: Record<string, unknown> = {}
    for (let index = 0; index < 25; index += 1) nested = { child: nested }
    expect(() => exportAssistantChat(withTurn({ ...TURN, tool_calls: [{ ...TOOL, arguments: nested }] }))).toThrow('deeply nested')
  })

  it('should retain an explicit bounded export budget above the storage limit', () => {
    const turns = Array.from({ length: 27 }, (_value: unknown, index: number): TreeAssistantTurn => ({
      ...TURN, request_id: `request-${index}`, proposals: [{
        ...PROPOSAL, id: `proposal-${index}`, action: {
          kind: 'plan', run: false,
          steps: Array.from({ length: 20 }, (_step: unknown, step: number) => ({
            id: `step-${step}`, parent: null, prompt: 'x'.repeat(32_000), converters: [],
          })),
        },
      }],
    }))
    const original = checkpoint({
      session: { session_id: 'private-server-capability', workspace_id: WORKSPACE_ID, model: 'configured-model', turns },
    })
    expect(() => exportAssistantChat(original)).toThrow('16 MB UTF-8')
    expect(localStorage.length).toBe(0)
  })
})
