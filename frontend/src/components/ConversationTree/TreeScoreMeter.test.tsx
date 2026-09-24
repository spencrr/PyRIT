import { FluentProvider, webLightTheme } from '@fluentui/react-components'
import { render, screen } from '@testing-library/react'

import type { TreeScoreRun } from '@/types'

import { DEFAULT_TREE_SETTINGS } from './treeModel'
import TreeScoreMeter from './TreeScoreMeter'

const SCORER = { scorer_id: 'judge', scorer_type: 'Scale', identifier_hash: 'hash', score_type: 'float_scale' as const, scope: 'response' as const, highIsRisk: true }
const SETTINGS = { ...DEFAULT_TREE_SETTINGS, scorers: [SCORER], primaryScorerId: 'judge' }
function show(runs: TreeScoreRun[], highIsRisk = true): void {
  render(<FluentProvider theme={webLightTheme}><TreeScoreMeter node={{ scoreRuns: runs }}
    settings={{ ...SETTINGS, scorers: [{ ...SCORER, highIsRisk }] }} /></FluentProvider>)
}
function run(value: string | null, status = 'complete'): TreeScoreRun {
  return { id: 'run', scorerId: 'judge', scorerHash: 'hash', status: 'complete', scores: [{
    id: 'score', scorer_type: 'Scale', message_piece_id: 'piece', score_type: 'float_scale',
    score_value: value, status, timestamp: new Date().toISOString(),
  }] }
}
describe('TreeScoreMeter', () => {
  it('renders nothing when the workspace has no scorer', () => {
    render(<FluentProvider theme={webLightTheme}><TreeScoreMeter node={{}} settings={DEFAULT_TREE_SETTINGS} /></FluentProvider>)
    expect(screen.queryByText(/scor/i)).not.toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })
  it('shows a read-only value scale, not a human-input slider', () => {
    show([run('0.8')])
    expect(screen.getByRole('meter')).toHaveAttribute('aria-valuenow', '0.8')
    expect(screen.getByText('Scale: 0.8')).toBeVisible()
    expect(screen.queryByRole('slider')).not.toBeInTheDocument()
  })
  it('does not color unknown or inapplicable scores as zero', () => {
    show([run(null, 'undetermined')])
    expect(screen.getByText('Undetermined')).toBeVisible()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })
  it('does not silently aggregate multiple canonical scores', () => {
    const result = run('0.5')
    show([{ ...result, scores: [...result.scores, { ...result.scores[0], id: 'second', score_value: '0.9' }] }])
    expect(screen.getByText('2 scores')).toBeVisible()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })
})
