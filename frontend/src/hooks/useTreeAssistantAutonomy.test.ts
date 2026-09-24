import { act, renderHook } from '@testing-library/react'

import { createTreeWorkspace, getTreeSettings } from '@/components/ConversationTree/treeModel'
import type { TreeAssistantProposal, TreeAssistantSequence, TreeWorkspace } from '@/types'

import { useTreeAssistantAutonomy } from './useTreeAssistantAutonomy'

function fixture(): TreeWorkspace {
  const workspace = createTreeWorkspace({
    name: 'Auto mode', targetRegistryName: 'target', targetIdentifierHash: 'hash', labels: {}, systemPrompt: '',
  })
  return { ...workspace, settings: { ...getTreeSettings(workspace), autoRun: true, autoScore: true, scorers: [
    { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale', highIsRisk: true, scope: 'response' },
  ] } }
}

function addition(workspace: TreeWorkspace, id = 'add'): TreeAssistantProposal {
  return {
    id, workspace_id: workspace.id, base_revision: workspace.revision, summary: 'New draft', status: 'pending',
    action: { kind: 'mutate', commands: [
      { type: 'add', parentId: null, prompt: 'First probe', converters: [{ type: 'Base64Converter', params: {} }] },
    ] },
  }
}

describe('useTreeAssistantAutonomy', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('deducts frozen new-draft execution costs exactly once before the next planning turn', async () => {
    let workspace = fixture()
    const request = jest.fn<ReturnType<TreeAssistantSequence['request']>, Parameters<TreeAssistantSequence['request']>>()
      .mockImplementationOnce(async (message) => ({
        request_id: 'first', message, reply: 'Create', proposals: [addition(workspace)],
      }))
      .mockImplementationOnce(async (message) => ({ request_id: 'second', message, reply: 'Done', proposals: [] }))
    const resolve = jest.fn<ReturnType<TreeAssistantSequence['resolve']>, Parameters<TreeAssistantSequence['resolve']>>()
      .mockImplementation(async (_proposal, grant, review) => {
        expect(grant.remaining_operations).toBe(7)
        expect(review).toMatchObject({ kind: 'mutate', run: true, operations: 3 })
        expect(review.nodeIds).toEqual(review.addedNodeIds)
        expect(Object.isFrozen(review)).toBe(true)
        if ('workspace' in review) workspace = review.workspace
        return { status: 'applied', revision: 1, detail: 'New draft executed' }
      })
    const runSequence = async (work: (sequence: TreeAssistantSequence) => Promise<void>): Promise<void> => { await work({ request, resolve }) }
    const hook = renderHook(() => useTreeAssistantAutonomy({
      workspace, getWorkspace: () => workspace, selectedId: null, active: true, disabled: false, runSequence,
    }))
    expect(hook.result.current.state.kind).toBe('off')
    await act(async () => { await hook.result.current.runAutonomy('Explore from empty', 7, null) })
    expect(request).toHaveBeenNthCalledWith(1, 'Explore from empty', {
      root_node_id: null, goal: 'Explore from empty', remaining_operations: 7, remaining_turns: 9,
    })
    expect(request).toHaveBeenNthCalledWith(2, 'Explore from empty', {
      root_node_id: null, goal: 'Explore from empty', remaining_operations: 4, remaining_turns: 8,
    })
    expect(resolve).toHaveBeenCalledTimes(1)
    expect(hook.result.current.grant).toBeNull()
    expect(hook.result.current.state.kind).toBe('finished')
  })

  it('rejects the complete auto-run proposal if scoring and converter costs exceed the grant', async () => {
    const workspace = fixture()
    const request = jest.fn().mockResolvedValue({ request_id: 'first', message: 'Explore', reply: 'Create', proposals: [addition(workspace)] })
    const resolve = jest.fn()
    const runSequence = async (work: (sequence: TreeAssistantSequence) => Promise<void>): Promise<void> => { await work({ request, resolve }) }
    const hook = renderHook(() => useTreeAssistantAutonomy({
      workspace, selectedId: null, active: true, disabled: false, runSequence,
    }))
    await act(async () => { await expect(hook.result.current.runAutonomy('Explore', 2, null)).rejects.toThrow(/budget/) })
    expect(resolve).not.toHaveBeenCalled()
    expect(workspace.nodes).toEqual([])
    expect(hook.result.current.grant).toBeNull()
  })

  it('bounds zero-cost draft-only actions by the ten-turn planning cap', async () => {
    const workspace = fixture()
    const request = jest.fn<ReturnType<TreeAssistantSequence['request']>, Parameters<TreeAssistantSequence['request']>>()
      .mockImplementation(async (message, grant) => {
        const proposed = addition(workspace, `add-${grant.remaining_turns}`)
        return { request_id: `turn-${grant.remaining_turns}`, message, reply: 'Draft', proposals: [
          { ...proposed, action: { kind: 'mutate', run: false, commands: [{ type: 'add', parentId: null, prompt: 'Draft' }] } },
        ] }
      })
    const resolve = jest.fn().mockResolvedValue({ status: 'applied', revision: 1, detail: 'Saved draft' })
    const runSequence = async (work: (sequence: TreeAssistantSequence) => Promise<void>): Promise<void> => { await work({ request, resolve }) }
    const hook = renderHook(() => useTreeAssistantAutonomy({
      workspace, selectedId: null, active: true, disabled: false, runSequence,
    }))
    await act(async () => { await hook.result.current.runAutonomy('Draft branches', 1, null) })
    expect(request).toHaveBeenCalledTimes(10)
    expect(resolve).toHaveBeenCalledTimes(10)
    expect(request.mock.calls[9][1]).toMatchObject({ remaining_operations: 1, remaining_turns: 0 })
    expect(hook.result.current.autonomyStatus).toContain('ten-turn planning limit')
    expect(hook.result.current.grant).toBeNull()
  })

  it.each(['missing', ''])('rejects invalid subtree scope %j even when whole-workspace planning would be valid', async (scope) => {
    const workspace = fixture()
    const request = jest.fn()
    const resolve = jest.fn()
    const runSequence = async (work: (sequence: TreeAssistantSequence) => Promise<void>): Promise<void> => { await work({ request, resolve }) }
    const hook = renderHook(() => useTreeAssistantAutonomy({
      workspace, selectedId: null, active: true, disabled: false, runSequence,
    }))
    await act(async () => { await expect(hook.result.current.runAutonomy('Explore', 1, scope)).rejects.toThrow(/missing or pruned/) })
    expect(request).not.toHaveBeenCalled()
    expect(resolve).not.toHaveBeenCalled()
  })
})
