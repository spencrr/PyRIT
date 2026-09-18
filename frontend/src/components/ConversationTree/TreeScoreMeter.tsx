import { Text } from '@fluentui/react-components'

import type { TreeNode, TreeSettings } from '@/types'

import { useConversationTreeStyles } from './ConversationTree.styles'

interface TreeScoreMeterProps {
  node: Pick<TreeNode, 'scoreRuns'>
  settings: TreeSettings
}

export default function TreeScoreMeter({ node, settings }: TreeScoreMeterProps) {
  const styles = useConversationTreeStyles()
  const primary = settings.scorers.find((scorer) => scorer.scorer_id === settings.primaryScorerId) ?? settings.scorers[0]
  if (!primary) return null
  const runs = node.scoreRuns?.filter((result) =>
    result.scorerId === primary.scorer_id && result.scorerHash === primary.identifier_hash)
  const run = runs?.[runs.length - 1]
  const score = run?.scores.length === 1 ? run.scores[0] : undefined
  const raw = score?.score_value
  const value = score && score.status !== 'undetermined' && raw !== null && raw !== undefined
    ? score.score_type === 'true_false' ? raw.toLowerCase() === 'true' ? 1 : raw.toLowerCase() === 'false' ? 0 : undefined
      : score.score_type === 'float_scale' && Number.isFinite(Number(raw)) ? Number(raw) : undefined
    : undefined
  const valid = value !== undefined && value >= 0 && value <= 1
  const risk = valid ? primary.highIsRisk ? value : 1 - value : undefined
  const label = !run ? 'Unscored' : run.status === 'error' ? 'Scoring error'
      : run.status === 'not_applicable' ? 'Not applicable'
        : run.scores.length > 1 ? `${run.scores.length} scores`
          : valid ? `${primary.scorer_type}: ${raw}` : 'Undetermined'
  return (
    <div className={styles.scoreIndicator}>
      <Text size={200}>{label}</Text>
      {risk !== undefined && (
        <div className={styles.scoreTrack} role="meter" aria-label={`${label}; ${primary.highIsRisk ? 'higher values indicate more risk' : 'lower values indicate more risk'}`}
          aria-valuemin={0} aria-valuemax={1} aria-valuenow={value}>
          <span className={styles.scoreMarker} style={{ left: `${risk * 100}%` }} />
        </div>
      )}
    </div>
  )
}
