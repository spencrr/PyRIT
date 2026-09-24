import type { BackendMessage, TreeNode, TreeScoreRun, TreeWorkspace } from '@/types'

import { applyTreeCommand, createTreeWorkspace, getCurrentAttemptId, getTreeSettings, parseTreeWorkspace } from './treeModel'
import { captureTreeRunResult, formatTreeRunResult } from './treeRunResult'

const TIME = '2026-09-18T12:00:00.000Z'
const SCORER = { scorer_id: 'scorer', scorer_type: 'TestScorer', identifier_hash: 'scorer-hash', score_type: 'true_false' as const,
  scope: 'response' as const, highIsRisk: true }

function fixture(count = 1): TreeWorkspace {
  return parseTreeWorkspace(JSON.stringify({
    ...createTreeWorkspace({
      name: 'Result', targetRegistryName: 'target', targetIdentifierHash: 'target-hash', systemPrompt: '', labels: {},
    }),
    nodes: Array.from({ length: count }, (_value: unknown, index: number): TreeNode => ({
      id: `node-${index}`, parentId: null, prompt: `Prompt ${index}`, converters: [],
      status: 'draft', pruned: false, kept: false,
    })),
  }))
}

function completed(node: TreeNode): TreeNode {
  return {
    ...node, status: 'completed', attackResultId: `${node.id}-attack`, conversationId: `${node.id}-conversation`,
    lastSequence: 1,
    messages: ['user', 'assistant'].map((role: string, index: number): BackendMessage => ({
      turn_number: index, role, created_at: TIME,
      message_pieces: [{
        id: `${node.id}-piece-${index}`, original_value_data_type: 'text', converted_value_data_type: 'text',
        original_value: role === 'user' ? node.prompt : 'Response', converted_value: role === 'user' ? node.prompt : 'Response',
        response_error: 'none', scores: [],
      }],
    })),
  }
}

function score(node: TreeNode, status: TreeScoreRun['status'] = 'complete', id = `${node.id}-score`): TreeScoreRun {
  return {
    id, scorerId: SCORER.scorer_id, scorerHash: SCORER.identifier_hash, status,
    scores: status === 'complete' ? [{
      id: `${id}-verdict`, message_piece_id: `${node.id}-piece-1`, scorer_type: SCORER.scorer_type,
      score_type: 'true_false', score_value: 'true', score_rationale: 'Observed evidence', timestamp: TIME,
    }] : [],
    ...(status === 'error' ? { error: 'Scorer failed' } : {}),
  }
}

describe('treeRunResult', () => {
  beforeEach(() => { jest.clearAllMocks() })

  it('should capture detached immutable attempts that cannot change after retry or later mutation', () => {
    const before = fixture()
    const after = { ...before, revision: 3, nodes: before.nodes.map(completed) }
    const result = captureTreeRunResult({ before, after, nodeIds: ['node-0'], kind: 'run', stopped: false, id: 'run-1' })
    const serialized = JSON.stringify(result)
    expect(result.complete).toBe(true)
    expect(result.counts).toEqual({ completed: 1, error: 0, skipped: 0, notApplicable: 0 })
    expect(Object.isFrozen(result)).toBe(true)
    expect(Object.isFrozen(result.nodes[0].attempt?.messages?.[0].message_pieces[0])).toBe(true)
    expect(Object.isFrozen(after.nodes[0])).toBe(false)
    const retried = applyTreeCommand(after, { type: 'retry', nodeId: 'node-0', scope: 'node' })
    expect(retried.nodes[0].status).toBe('draft')
    expect(result.nodes[0].attemptId).not.toBe(getCurrentAttemptId(retried.nodes[0]))
    const archived = captureTreeRunResult({ before, after: retried, nodeIds: ['node-0'], kind: 'run', stopped: false })
    expect(archived.nodes[0].attempt).toEqual(result.nodes[0].attempt)
    const piece = after.nodes[0].messages?.[1].message_pieces[0]
    if (!piece) throw new Error('Expected response piece')
    piece.converted_value = 'Changed response'
    expect(JSON.stringify(result)).toBe(serialized)
    expect(formatTreeRunResult(result)).toBe('1/1 nodes completed.')
  })

  it('should distinguish completed, failed, and skipped attempts without counting older evidence', () => {
    const before = fixture(4)
    before.nodes[3] = completed(before.nodes[3])
    const after: TreeWorkspace = { ...before, nodes: [
      completed(before.nodes[0]),
      { ...before.nodes[1], status: 'error', error: 'Failed before sending' },
      before.nodes[2], before.nodes[3],
    ] }
    const result = captureTreeRunResult({
      before, after, nodeIds: before.nodes.map((node: TreeNode) => node.id), kind: 'run', stopped: true,
    })
    expect(result.nodes.map((node) => node.outcome)).toEqual(['completed', 'error', 'skipped', 'skipped'])
    expect(result.complete).toBe(false)
    expect(formatTreeRunResult(result)).toBe('1/4 nodes completed; 1 failed; 2 skipped; stopped by the user.')
  })

  it.each(['run', 'score'] as const)('should never count old successful evidence as new %s work', (kind: 'run' | 'score') => {
    const draft = fixture()
    const node = completed(draft.nodes[0])
    const before: TreeWorkspace = {
      ...draft, nodes: [{ ...node, scoreRuns: [score(node)] }],
      settings: { ...getTreeSettings(draft), autoScore: true, scorers: [SCORER] },
    }
    const retried = applyTreeCommand(before, { type: 'retry', nodeId: node.id, scope: 'node' })
    const retryResponse = completed(retried.nodes[0])
    const later = { ...retried, nodes: [{ ...retryResponse, scoreRuns: [score(retryResponse, 'complete', 'new-score')] }] }
    for (const stopped of [false, true]) {
      for (const after of [before, later]) {
        const result = captureTreeRunResult({ before, after, nodeIds: [node.id], kind, stopped })
        expect(result.complete).toBe(false)
        expect(result.counts).toEqual({ completed: 0, error: 0, skipped: 1, notApplicable: 0 })
        expect(result.scoringCounts.completed).toBe(0)
        expect(result.nodes[0].attemptId).toBe(getCurrentAttemptId(node))
        expect(result.nodes[0].scorers[0].result).toBeUndefined()
      }
    }
  })

  it('should not count running state or an unrelated attempt as completed work', () => {
    const draft = fixture()
    const before: TreeWorkspace = { ...draft, nodes: [{ ...draft.nodes[0], status: 'running' }] }
    const differentAttempt = { ...before, nodes: [completed({ ...before.nodes[0], attemptId: 'unrelated-attempt' })] }
    for (const after of [before, differentAttempt]) {
      const result = captureTreeRunResult({ before, after, nodeIds: ['node-0'], kind: 'run', stopped: true })
      expect(result.complete).toBe(false)
      expect(result.counts.completed).toBe(0)
      expect(result.counts.skipped).toBe(1)
      expect(result.nodes[0].attempt).toBeUndefined()
    }
    const settled = captureTreeRunResult({
      before, after: { ...before, nodes: before.nodes.map(completed) }, nodeIds: ['node-0'], kind: 'run', stopped: true,
    })
    expect(settled.counts.completed).toBe(1)
    expect(settled.complete).toBe(false)
  })

  it('should capture exact newly produced configured scorer outcomes and budget from the approved snapshot', () => {
    const draft = fixture(4)
    const before: TreeWorkspace = {
      ...draft, nodes: draft.nodes.map(completed),
      settings: { ...getTreeSettings(draft), scorers: [SCORER], operationBudget: 20 },
    }
    before.nodes[3].scoreRuns = [score(before.nodes[3])]
    const after = { ...before, nodes: before.nodes.map((node: TreeNode, index: number): TreeNode =>
      index === 3 ? node : { ...node, scoreRuns: [score(node, index === 0 ? 'complete' : index === 1 ? 'error' : 'not_applicable')] }) }
    const result = captureTreeRunResult({ before, after, nodeIds: before.nodes.map((node: TreeNode) => node.id), kind: 'score', stopped: false })
    expect(result.nodes.map((node) => node.outcome)).toEqual(['completed', 'error', 'not_applicable', 'skipped'])
    expect(result.scoringCounts).toEqual({ completed: 1, error: 1, notApplicable: 1, skipped: 1 })
    expect(result.nodes[0].scorers[0].result).toEqual(after.nodes[0].scoreRuns?.[0])
    expect(result.operations).toBe(4)
    expect(result.budget).toBe(20)
    const verdict = after.nodes[0].scoreRuns?.[0].scores[0]
    if (!verdict) throw new Error('Expected score evidence')
    verdict.score_value = 'false'
    expect(result.nodes[0].scorers[0].result?.scores[0].score_value).toBe('true')
    expect(formatTreeRunResult(result)).toContain('1 scorer evaluations not applicable (no scores produced)')
    expect(result.complete).toBe(false)
  })

  it('should require configured auto-scoring to complete and count only matching scorer identities', () => {
    const draft = fixture()
    const before = {
      ...draft, nodes: [{ ...draft.nodes[0], converters: [{ type: 'Converter', params: {} }] }],
      settings: { ...getTreeSettings(draft), autoScore: true, scorers: [SCORER] },
    }
    const node = completed(before.nodes[0])
    const after = { ...before, nodes: [{ ...node, scoreRuns: [{ ...score(node), scorerHash: 'different' }] }] }
    const result = captureTreeRunResult({ before, after, nodeIds: [node.id], kind: 'run', stopped: false })
    expect(result.operations).toBe(3)
    expect(result.counts.skipped).toBe(1)
    expect(result.nodes[0].attempt?.status).toBe('completed')
    expect(result.complete).toBe(false)
    after.nodes[0].scoreRuns = [score(node, 'not_applicable')]
    const notApplicable = captureTreeRunResult({ before, after, nodeIds: [node.id], kind: 'run', stopped: false })
    expect(notApplicable.counts.notApplicable).toBe(1)
    expect(notApplicable.complete).toBe(false)
    after.nodes[0].scoreRuns = [score(node)]
    expect(captureTreeRunResult({ before, after, nodeIds: [node.id], kind: 'run', stopped: false }).complete).toBe(true)
  })

  it('should not claim successful scoring with no configured scorers or empty scores', () => {
    const before = fixture()
    before.nodes = before.nodes.map(completed)
    expect(captureTreeRunResult({ before, after: before, nodeIds: ['node-0'], kind: 'score', stopped: false }).complete).toBe(false)
    before.settings = { ...getTreeSettings(before), scorers: [SCORER] }
    const after = { ...before, nodes: [{ ...before.nodes[0], scoreRuns: [{ ...score(before.nodes[0]), scores: [] }] }] }
    const result = captureTreeRunResult({ before, after, nodeIds: ['node-0'], kind: 'score', stopped: false })
    expect(result.nodes[0].outcome).toBe('not_applicable')
    expect(result.nodes[0].scorers[0].result?.status).toBe('complete')
    expect(result.complete).toBe(false)
  })

  it('should preserve errors and persistence failures even when all received evidence is completed', () => {
    const before = fixture()
    const after = { ...before, nodes: before.nodes.map(completed) }
    const result = captureTreeRunResult({
      before, after, nodeIds: ['node-0', 'node-0'], kind: 'run', stopped: false, persisted: false, error: 'Save failed', effectiveConcurrency: 1,
    })
    expect(result.complete).toBe(false)
    expect(result.operations).toBe(1)
    expect(result.nodeIds).toEqual(['node-0'])
    expect(formatTreeRunResult(result)).toContain('results require recovery before they are saved; Save failed')
    expect(() => captureTreeRunResult({ before, after: { ...after, id: 'other' }, nodeIds: [], kind: 'run', stopped: false })).toThrow('different workspace')
    expect(() => captureTreeRunResult({ before, after, nodeIds: ['missing'], kind: 'run', stopped: false })).toThrow('approved snapshot')
  })
})
