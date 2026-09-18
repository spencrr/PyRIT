import type {
  TreeAttempt, TreeNode, TreeRunCapture, TreeRunCounts, TreeRunNodeResult, TreeRunOutcome,
  TreeRunResult, TreeRunScorerResult, TreeScoreRun, TreeScorerSelection,
} from '@/types'

import { getCurrentAttemptId, getTreeAttempts, getTreeSettings } from './treeModel'

function freezeSnapshot<T>(value: T): T {
  if (value !== null && typeof value === 'object') {
    for (const child of Object.values(value)) freezeSnapshot(child)
    Object.freeze(value)
  }
  return value
}

function attemptFor(node: TreeNode | undefined, attemptId: string): TreeAttempt | undefined {
  return node ? getTreeAttempts(node).find((attempt: TreeAttempt) => attempt.attemptId === attemptId) : undefined
}

function scoreOutcome(result: TreeScoreRun | undefined): TreeRunOutcome {
  if (!result) return 'skipped'
  if (result.status === 'error') return 'error'
  return result.status === 'not_applicable' || result.scores.length === 0 ? 'not_applicable' : 'completed'
}

function aggregate(outcomes: readonly TreeRunOutcome[]): TreeRunOutcome {
  if (outcomes.includes('error')) return 'error'
  if (outcomes.length === 0 || outcomes.includes('skipped')) return 'skipped'
  if (outcomes.includes('not_applicable')) return 'not_applicable'
  return 'completed'
}

function count(outcomes: readonly TreeRunOutcome[]): TreeRunCounts {
  return {
    completed: outcomes.filter((outcome: TreeRunOutcome) => outcome === 'completed').length,
    error: outcomes.filter((outcome: TreeRunOutcome) => outcome === 'error').length,
    skipped: outcomes.filter((outcome: TreeRunOutcome) => outcome === 'skipped').length,
    notApplicable: outcomes.filter((outcome: TreeRunOutcome) => outcome === 'not_applicable').length,
  }
}

/**
 * Captures only the approved attempts and newly recorded configured scorer evaluations.
 * Later retries, rescoring, and edits cannot change this detached, deeply frozen result.
 */
export function captureTreeRunResult(input: TreeRunCapture): TreeRunResult {
  const { before, after, kind } = input
  if (before.id !== after.id) throw new Error('Run result belongs to a different workspace.')
  const nodeIds = [...new Set(input.nodeIds)]
  const settings = getTreeSettings(before)
  const configuredScorers = kind === 'score' || settings.autoScore ? settings.scorers : []
  const nodes = nodeIds.map((nodeId: string): TreeRunNodeResult => {
    const original = before.nodes.find((node: TreeNode) => node.id === nodeId)
    if (!original) throw new Error('Run result contains a node outside its approved snapshot.')
    const attemptId = getCurrentAttemptId(original)
    const previous = attemptFor(original, attemptId)
    const attempt = attemptFor(after.nodes.find((node: TreeNode) => node.id === nodeId), attemptId)
    const oldScoreIds = new Set((previous?.scoreRuns ?? []).map((run: TreeScoreRun) => run.id))
    const scorers = configuredScorers.map((scorer: TreeScorerSelection): TreeRunScorerResult => {
      const results = attempt?.scoreRuns?.filter((run: TreeScoreRun) =>
        run.scorerId === scorer.scorer_id && run.scorerHash === scorer.identifier_hash && !oldScoreIds.has(run.id)) ?? []
      const result = results[results.length - 1]
      return { scorerId: scorer.scorer_id, scorerHash: scorer.identifier_hash, outcome: scoreOutcome(result), result }
    })
    let outcome: TreeRunOutcome
    if (kind === 'score') {
      outcome = aggregate(scorers.map((scorer: TreeRunScorerResult) => scorer.outcome))
    } else if (previous || !attempt) {
      outcome = 'skipped'
    } else if (attempt.status === 'error') {
      outcome = 'error'
    } else {
      outcome = configuredScorers.length ? aggregate(scorers.map((scorer: TreeRunScorerResult) => scorer.outcome)) : 'completed'
    }
    return { nodeId, attemptId, outcome, attempt, scorers }
  })
  const counts = count(nodes.map((node: TreeRunNodeResult) => node.outcome))
  const scoringCounts = count(nodes.flatMap((node: TreeRunNodeResult) =>
    node.scorers.map((scorer: TreeRunScorerResult) => scorer.outcome)))
  const operations = nodeIds.reduce((total: number, nodeId: string) => {
    const node = before.nodes.find((entry: TreeNode) => entry.id === nodeId)
    return total + (kind === 'score' ? configuredScorers.length : 1 + (node?.converters.length ?? 0) + configuredScorers.length)
  }, 0)
  const persisted = input.persisted !== false
  const result: TreeRunResult = {
    id: input.id,
    workspaceId: before.id,
    revision: after.revision,
    kind,
    nodeIds,
    stopped: input.stopped,
    error: input.error,
    persisted,
    effectiveConcurrency: input.effectiveConcurrency,
    operations,
    budget: settings.operationBudget,
    nodes,
    counts,
    scoringCounts,
    complete: nodes.length > 0 && counts.completed === nodes.length && !input.stopped && !input.error && persisted,
  }
  return freezeSnapshot(JSON.parse(JSON.stringify(result)) as TreeRunResult)
}

/** Formats the captured result only; it never reads the live workspace. */
export function formatTreeRunResult(result: TreeRunResult): string {
  const { counts, scoringCounts } = result
  const subject = result.kind === 'score' ? 'responses scored' : result.nodes.some((node: TreeRunNodeResult) => node.scorers.length > 0)
    ? 'nodes completed with configured scoring' : 'nodes completed'
  const details = [`${counts.completed}/${result.nodeIds.length} ${subject}`]
  if (counts.error) details.push(`${counts.error} failed`)
  if (counts.skipped) details.push(`${counts.skipped} skipped`)
  if (scoringCounts.notApplicable) {
    details.push(`${scoringCounts.notApplicable} scorer evaluations not applicable (no scores produced)`)
  }
  if (scoringCounts.error) details.push(`${scoringCounts.error} scorer evaluations failed`)
  if (scoringCounts.skipped) details.push(`${scoringCounts.skipped} scorer evaluations skipped`)
  if (result.stopped) details.push('stopped by the user')
  if (!result.persisted) details.push('results require recovery before they are saved')
  if (result.error) details.push(result.error)
  return `${details.join('; ')}.`
}
