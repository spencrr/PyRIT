import { StrictMode } from 'react'

import { act, renderHook, waitFor } from '@testing-library/react'

import { treeAssistantApi } from '@/services/api'
import type { TreeAssistantCheckpoint, TreeAssistantProposal, TreeAssistantTurn, TreeWorkspace } from '@/types'
import { createTreeWorkspace, applyTreeCommand } from '@/components/ConversationTree/treeModel'
import { deleteAssistantCheckpoint, saveAssistantCheckpoint } from '@/components/ConversationTree/treeAssistantStorage'

import { useTreeAssistantSession as useSession } from './useTreeAssistantSession'
import { useTreeAssistantAutonomy } from './useTreeAssistantAutonomy'

function useTreeAssistantSession(options: Parameters<typeof useSession>[0]) {
  const session = useSession(options)
  return { ...session, ...useTreeAssistantAutonomy({ ...options, runSequence: session.runSequence }) }
}

let mockStored: TreeAssistantCheckpoint | null = null
jest.mock('@/components/ConversationTree/treeAssistantStorage', () => ({
  loadAssistantCheckpoint: () => mockStored,
  deleteAssistantCheckpoint: jest.fn(() => { mockStored = null }),
  saveAssistantCheckpoint: jest.fn((checkpoint: TreeAssistantCheckpoint) => {
    mockStored = JSON.parse(JSON.stringify({ ...checkpoint, revision: checkpoint.revision + 1 }))
    return mockStored
  }),
}))
jest.mock('@/services/api', () => ({ treeAssistantApi: {
  createSession: jest.fn(), getSession: jest.fn(), sendMessage: jest.fn(), recordResult: jest.fn(), deleteSession: jest.fn(),
} }))

function fixture(): TreeWorkspace {
  return applyTreeCommand(createTreeWorkspace({ name: 'Test', targetRegistryName: 'target', targetIdentifierHash: 'hash', labels: {}, systemPrompt: '' }),
    { type: 'add', parentId: null, prompt: 'Root prompt' })
}

async function start(result: { current: ReturnType<typeof useTreeAssistantSession> }): Promise<void> {
  await waitFor(() => expect(result.current.writable).toBe(true))
  await act(async () => { await result.current.start() })
}

describe('useTreeAssistantSession', () => {
  const api = jest.mocked(treeAssistantApi)
  beforeEach(() => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
      request: jest.fn(async (_name: string, _options: { ifAvailable?: boolean }, callback: (lock: Lock) => Promise<void>) =>
        callback({ name: 'test', mode: 'exclusive' })),
    } })
    mockStored = null
    jest.clearAllMocks()
    for (const method of Object.values(api)) method.mockReset()
    jest.mocked(saveAssistantCheckpoint).mockImplementation((checkpoint) => {
      mockStored = JSON.parse(JSON.stringify({ ...checkpoint, revision: checkpoint.revision + 1 }))
      return mockStored as TreeAssistantCheckpoint
    })
    api.createSession.mockImplementation(async (workspaceId, history = []) => ({ workspace_id: workspaceId, session_id: 'session', model: 'model', turns: history }))
    api.recordResult.mockImplementation(async (_session, id, receipt) => ({
      id, workspace_id: mockStored?.workspaceId ?? '', base_revision: 0, summary: 'Proposal', action: { kind: 'run', node_ids: [] },
      status: receipt.status, result: receipt,
    }))
    api.sendMessage.mockImplementation(async (_session, request) => ({ request_id: request.request_id, message: request.message, reply: 'No further actions.', proposals: [] }))
  })

  it('supports manual sessions without mounting autonomy orchestration', async () => {
    const workspace = fixture()
    const hook = renderHook(() => useSession({ workspace, selectedId: null, active: true, disabled: false, onApply: jest.fn() }))
    await waitFor(() => expect(hook.result.current.writable).toBe(true))
    await act(async () => { await hook.result.current.start() })
    expect(hook.result.current).not.toHaveProperty('runAutonomy')
    expect(hook.result.current).not.toHaveProperty('grant')
    expect(hook.result.current.connection).toBe('online')
    act(() => { hook.result.current.editDraft('Review the tree') })
    await act(async () => { await hook.result.current.send() })
    expect(hook.result.current.operation).toBe('idle')
    expect(hook.result.current.journal.kind).toBe('ready')
    expect(api.sendMessage.mock.calls[0][1].context).not.toHaveProperty('autonomy')
  })

  it('persists host preconditions before requests without adding them to backend payloads', async () => {
    const workspace = fixture()
    const proposed: TreeAssistantProposal = { id: 'proposal', workspace_id: workspace.id, base_revision: workspace.revision,
      summary: 'Add child', status: 'pending', action: { kind: 'mutate', commands: [{ type: 'add', parentId: workspace.nodes[0].id, prompt: 'Child' }] } }
    api.sendMessage.mockImplementation(async (_id, request) => {
      expect(mockStored?.preconditions?.[request.request_id]).toMatchObject({ workspaceId: workspace.id, baseRevision: workspace.revision })
      expect(Object.keys(request).sort()).toEqual(['context', 'message', 'request_id'])
      return { request_id: request.request_id, message: request.message, reply: 'Review', proposals: [proposed] }
    })
    const onApply = jest.fn().mockResolvedValue({ status: 'applied', revision: 2, detail: 'Applied' })
    let liveWorkspace = workspace
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, getWorkspace: () => liveWorkspace, selectedId: null, active: true, disabled: false, onApply,
    }))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Explore') })
    await act(async () => { await hook.result.current.send() })
    const captured = mockStored?.preconditions?.[api.sendMessage.mock.calls[0][1].request_id]
    liveWorkspace = { ...applyTreeCommand(workspace, { type: 'move', nodeId: workspace.nodes[0].id, position: { x: 150, y: 50 } }), revision: 1 }
    await act(async () => { await hook.result.current.decide(proposed, true) })
    expect(onApply).toHaveBeenCalledWith(proposed, undefined, expect.objectContaining({ precondition: captured, preparedRevision: 1 }))
    expect(mockStored?.preconditions).toEqual({})
  })

  it('does not allow reply recovery or sending to bypass an unreported receipt', async () => {
    const workspace = fixture()
    api.sendMessage.mockImplementation(async (_id, request) => ({
      request_id: request.request_id, message: request.message, reply: 'Review', proposals: [{
        id: 'proposal', workspace_id: workspace.id, base_revision: workspace.revision, summary: 'Run root', status: 'pending',
        action: { kind: 'run', node_ids: [workspace.nodes[0].id] },
      }],
    }))
    api.recordResult.mockRejectedValue(new Error('Network unavailable'))
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: null, active: true, disabled: false, onApply: jest.fn(),
    }))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Explore') })
    await act(async () => { await hook.result.current.send() })
    const proposed = hook.result.current.checkpoint.session?.turns[0].proposals[0]
    if (!proposed) throw new Error('Missing proposal')
    await act(async () => { await hook.result.current.decide(proposed, false) })
    expect(hook.result.current.journal.kind).toBe('receipt')
    await act(async () => {
      await hook.result.current.recoverReply()
      await hook.result.current.retryMessage()
      await hook.result.current.send()
    })
    expect(api.getSession).not.toHaveBeenCalled()
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    expect(hook.result.current.journal.kind).toBe('receipt')
  })

  it('never applies when the execution journal cannot be persisted', async () => {
    const workspace = fixture()
    const proposed: TreeAssistantProposal = { id: 'proposal', workspace_id: workspace.id, base_revision: workspace.revision,
      summary: 'Run root', status: 'pending', action: { kind: 'run', node_ids: [workspace.nodes[0].id] } }
    api.sendMessage.mockImplementation(async (_id, request) => ({
      request_id: request.request_id, message: request.message, reply: 'Review', proposals: [proposed],
    }))
    const onApply = jest.fn()
    const hook = renderHook(() => useTreeAssistantSession({ workspace, selectedId: null, active: true, disabled: false, onApply }))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Explore') })
    await act(async () => { await hook.result.current.send() })
    jest.mocked(saveAssistantCheckpoint).mockImplementationOnce(() => { throw new Error('Storage full') })
    await act(async () => { await hook.result.current.decide(proposed, true) })
    expect(hook.result.current.journal.kind).toBe('execution')
    expect(hook.result.current.checkpoint.executing?.precondition).toMatchObject({ workspaceId: workspace.id })
    expect(onApply).not.toHaveBeenCalled()
    expect(api.recordResult).not.toHaveBeenCalled()
    expect(hook.result.current.storageError).toContain('Storage full')
  })

  it('restores saved context after reload and backend expiry without rearming autonomy', async () => {
    const workspace = fixture()
    const options = { workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn() }
    const first = renderHook(() => useTreeAssistantSession(options))
    await start(first.result)
    act(() => { first.result.current.editDraft('Remember this discussion') })
    await act(async () => { await first.result.current.send() })
    act(() => { first.result.current.editDraft('Unsent draft') })
    first.unmount()
    const saved = mockStored
    const second = renderHook(() => useTreeAssistantSession(options))
    expect(second.result.current.checkpoint.draft).toBe('Unsent draft')
    expect(second.result.current.connected).toBe(false)
    expect(second.result.current.grant).toBeNull()
    api.getSession.mockRejectedValue({ isAxiosError: true, response: { status: 404, data: { detail: 'Expired' } } })
    await start(second.result)
    expect(api.createSession).toHaveBeenLastCalledWith(workspace.id, saved?.session?.turns)
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    expect(options.onApply).not.toHaveBeenCalled()
  })

  it('saves the exact pending message before sending and retries it without changing context', async () => {
    const workspace = fixture()
    const hook = renderHook(() => useTreeAssistantSession({ workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn() }))
    await start(hook.result)
    api.sendMessage.mockRejectedValueOnce(new Error('Lost response'))
    act(() => { hook.result.current.editDraft('Explore') })
    await act(async () => { await hook.result.current.send() })
    const pending = mockStored?.pendingMessage
    expect(pending?.message).toBe('Explore')
    await act(async () => { await hook.result.current.retryMessage() })
    expect(api.sendMessage.mock.calls[1][1]).toEqual(pending)
    expect(hook.result.current.checkpoint.session?.turns).toHaveLength(1)
    expect(hook.result.current.checkpoint.pendingMessage).toBeNull()
  })

  it('blocks provider calls when checkpoint storage fails', async () => {
    const workspace = fixture()
    const hook = renderHook(() => useTreeAssistantSession({ workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn() }))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Explore') })
    jest.mocked(saveAssistantCheckpoint).mockImplementationOnce(() => { throw new Error('Quota exceeded') })
    await act(async () => { await hook.result.current.send() })
    expect(hook.result.current.storageError).toContain('Quota')
    expect(api.sendMessage).not.toHaveBeenCalled()
    expect(hook.result.current.checkpoint.pendingMessage?.message).toBe('Explore')
  })

  it('journals before applying and never reapplies an interrupted action after remount', async () => {
    const workspace = fixture()
    let finish: ((value: { status: 'applied'; revision: number; detail: string }) => void) | undefined
    const onApply = jest.fn(() => new Promise<{ status: 'applied'; revision: number; detail: string }>((resolve) => { finish = resolve }))
    const proposal: TreeAssistantProposal = { id: 'proposal', workspace_id: workspace.id, base_revision: 0, summary: 'Add', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'add', parentId: workspace.nodes[0].id, prompt: 'Child' }] } }
    api.sendMessage.mockImplementation(async (_id, request) => ({ request_id: request.request_id, message: request.message, reply: 'Pending', proposals: [proposal] }))
    const options = { workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply }
    const hook = renderHook(() => useTreeAssistantSession(options))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Explore') })
    await act(async () => { await hook.result.current.send() })
    let action: Promise<void> | undefined
    act(() => { action = hook.result.current.decide(proposal, true) })
    expect(mockStored?.executing?.proposalId).toBe('proposal')
    hook.unmount()
    const reloaded = renderHook(() => useTreeAssistantSession(options))
    expect(reloaded.result.current.checkpoint.executing?.proposalId).toBe('proposal')
    expect(onApply).toHaveBeenCalledTimes(1)
    await act(async () => { finish?.({ status: 'applied', revision: 1, detail: 'Applied' }); await action })
    expect(api.recordResult).not.toHaveBeenCalled()
  })

  it('consumes bounded planning turns and stops before applying out-of-scope effects', async () => {
    const workspace = fixture()
    const onApply = jest.fn()
    const root = workspace.nodes[0].id
    const proposal: TreeAssistantProposal = { id: 'escape', workspace_id: workspace.id, base_revision: 0, summary: 'Outside', status: 'pending',
      action: { kind: 'mutate', commands: [{ type: 'add', parentId: null, prompt: 'Escapes subtree' }] } }
    api.sendMessage.mockImplementation(async (_id, request) => ({ request_id: request.request_id, message: request.message, reply: 'Pending', proposals: [proposal] }))
    const hook = renderHook(() => useTreeAssistantSession({ workspace, selectedId: root, active: true, disabled: false, onApply }))
    await start(hook.result)
    await act(async () => { await hook.result.current.runAutonomy('Explore scope', 3) })
    expect(onApply).not.toHaveBeenCalled()
    expect(hook.result.current.error).toContain('escapes')
    expect(api.sendMessage.mock.calls[0][1].context.autonomy).toMatchObject({ root_node_id: root, remaining_operations: 3, remaining_turns: 9 })
    expect(hook.result.current.grant).toBeNull()
  })

  it('stops at the exact operation budget without requesting another planning turn', async () => {
    const workspace = fixture()
    const root = workspace.nodes[0].id
    const onApply = jest.fn().mockResolvedValue({ status: 'applied', revision: 1, detail: 'One target send completed.' })
    const proposal: TreeAssistantProposal = { id: 'run', workspace_id: workspace.id, base_revision: 0,
      summary: 'Run root', status: 'pending', action: { kind: 'run', node_ids: [root] } }
    api.sendMessage.mockImplementation(async (_id, request) => ({
      request_id: request.request_id, message: request.message, reply: 'Run', proposals: [proposal],
    }))
    const hook = renderHook(() => useTreeAssistantSession({ workspace, selectedId: root, active: true, disabled: false, onApply }))
    await start(hook.result)
    await act(async () => { await hook.result.current.runAutonomy('Explore', 1) })
    expect(onApply).toHaveBeenCalledWith(proposal, expect.objectContaining({ root_node_id: root, remaining_operations: 1 }),
      expect.objectContaining({ nodeIds: [root], operations: 1 }))
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    expect(api.recordResult).toHaveBeenCalledTimes(1)
    expect(hook.result.current.autonomyStatus).toContain('budget exhausted')
    expect(hook.result.current.grant).toBeNull()
  })

  it.each(['stop', 'edit'] as const)('does not execute a pending autonomous proposal after %s', async (interrupt) => {
    let workspace = fixture()
    const root = workspace.nodes[0].id
    const onApply = jest.fn()
    let respond: ((turn: TreeAssistantTurn) => void) | undefined
    api.sendMessage.mockImplementation(() => new Promise((resolve) => { respond = resolve }))
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: root, active: true, disabled: false, onApply, getWorkspace: () => workspace,
    }))
    await start(hook.result)
    let task: Promise<void> | undefined
    act(() => { task = hook.result.current.runAutonomy('Explore', 2) })
    const pending = hook.result.current.checkpoint.pendingMessage
    expect(pending).not.toBeNull()
    act(() => {
      if (interrupt === 'stop') hook.result.current.stop()
      else workspace = { ...workspace, revision: workspace.revision + 1, systemPrompt: 'Edited while planning' }
    })
    await act(async () => {
      respond?.({ request_id: pending?.request_id ?? '', message: 'Explore', reply: 'Run', proposals: [{
        id: 'run', workspace_id: workspace.id, base_revision: 0, summary: 'Run', status: 'pending',
        action: { kind: 'run', node_ids: [root] },
      }] })
      await task
    })
    expect(onApply).not.toHaveBeenCalled()
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    expect(hook.result.current.grant).toBeNull()
  })

  it('does not rearm a planning grant when the assistant is hidden and reopened before its reply arrives', async () => {
    const workspace = fixture()
    const onApply = jest.fn()
    let respond: ((turn: TreeAssistantTurn) => void) | undefined
    api.sendMessage.mockImplementation(() => new Promise((resolve) => { respond = resolve }))
    const hook = renderHook(({ active }: { active: boolean }) => useTreeAssistantSession({
      workspace, selectedId: workspace.nodes[0].id, active, disabled: false, onApply,
    }), { initialProps: { active: true } })
    await start(hook.result)
    let task: Promise<void> | undefined
    act(() => { task = hook.result.current.runAutonomy('Explore', 2) })
    const pending = hook.result.current.checkpoint.pendingMessage
    hook.rerender({ active: false })
    hook.rerender({ active: true })
    await act(async () => {
      respond?.({ request_id: pending?.request_id ?? '', message: 'Explore', reply: 'Run', proposals: [{
        id: 'run', workspace_id: workspace.id, base_revision: workspace.revision, summary: 'Run', status: 'pending',
        action: { kind: 'run', node_ids: [workspace.nodes[0].id] },
      }] })
      await task
    })
    expect(onApply).not.toHaveBeenCalled()
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    expect(hook.result.current.grant).toBeNull()
    expect(hook.result.current.checkpoint.session?.turns[0].proposals[0].status).toBe('pending')
  })

  it('stays read-only when another tab owns the workspace chat writer lock', async () => {
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
      request: async (_name: string, _options: { ifAvailable?: boolean }, callback: (lock: Lock | null) => Promise<void>) => callback(null),
    } })
    const workspace = fixture()
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn(),
    }))
    await waitFor(() => expect(hook.result.current.writerError).toContain('another tab'))
    await act(async () => { await hook.result.current.start() })
    expect(api.createSession).not.toHaveBeenCalled()
    expect(saveAssistantCheckpoint).not.toHaveBeenCalled()
    expect(hook.result.current.writable).toBe(false)
  })

  it('acquires an asynchronous browser lock under StrictMode effect replay', async () => {
    let held = false
    Object.defineProperty(navigator, 'locks', { configurable: true, value: {
      request: async (_name: string, _options: { ifAvailable?: boolean }, callback: (lock: Lock | null) => Promise<void>) => {
        if (held) return callback(null)
        held = true
        await Promise.resolve()
        try { await callback({ name: 'test', mode: 'exclusive' }) } finally { held = false }
      },
    } })
    const workspace = fixture()
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn(),
    }), { wrapper: StrictMode })
    await start(hook.result)
    expect(hook.result.current.writerError).toBe('')
    expect(hook.result.current.connected).toBe(true)
    hook.unmount()
    await waitFor(() => expect(held).toBe(false))
  })

  it('clears only the expected stored revision after explicit export recovery', async () => {
    const workspace = fixture()
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn(),
    }))
    await start(hook.result)
    const savedRevision = hook.result.current.checkpoint.revision
    jest.mocked(saveAssistantCheckpoint).mockImplementationOnce(() => { throw new Error('Checkpoint capacity exceeded') })
    act(() => { hook.result.current.editDraft('Retained for export') })
    expect(hook.result.current.storageError).toContain('capacity')
    act(() => { hook.result.current.clearExportedChat() })
    expect(deleteAssistantCheckpoint).toHaveBeenCalledWith(workspace.id, savedRevision)
    expect(hook.result.current.storageError).toBe('')
    expect(hook.result.current.checkpoint.session).toBeNull()
    expect(api.deleteSession).not.toHaveBeenCalled()
  })

  it('archives older turns locally and restarts with only the latest model context', async () => {
    const workspace = fixture()
    const history = Array.from({ length: 50 }, (_, index): TreeAssistantTurn => ({
      request_id: `old-${index}`, message: `Question ${index}`, reply: `Reply ${index}`, proposals: [],
    }))
    api.createSession.mockResolvedValueOnce({ session_id: 'restored', workspace_id: workspace.id, model: 'model', turns: history })
    const hook = renderHook(() => useTreeAssistantSession({
      workspace, selectedId: workspace.nodes[0].id, active: true, disabled: false, onApply: jest.fn(),
    }))
    await start(hook.result)
    act(() => { hook.result.current.editDraft('Continue beyond fifty turns') })
    await act(async () => { await hook.result.current.send() })
    expect(hook.result.current.checkpoint.session?.turns).toHaveLength(50)
    expect(hook.result.current.checkpoint.archivedTurns).toEqual([history[0]])
    const recent = hook.result.current.checkpoint.session?.turns
    await act(async () => { await hook.result.current.start(true, true) })
    expect(api.createSession).toHaveBeenLastCalledWith(workspace.id, recent)
    expect(hook.result.current.checkpoint.archivedTurns).toEqual([history[0]])
    expect(hook.result.current.checkpoint.session?.turns).toHaveLength(50)
    expect(hook.result.current.grant).toBeNull()
    expect(api.sendMessage).toHaveBeenCalledTimes(1)
    await act(async () => { await hook.result.current.start(true) })
    expect(hook.result.current.checkpoint.archivedTurns).toEqual([])
    expect(hook.result.current.checkpoint.session?.turns).toEqual([])
  })
})
