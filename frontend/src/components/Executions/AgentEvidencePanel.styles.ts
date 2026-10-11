import { makeStyles, tokens } from '@fluentui/react-components'
import { mobileTouchTarget } from '@/styles/touchTargets'

export const useAgentEvidencePanelStyles = makeStyles({
  root: { display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS, minWidth: 0 },
  actions: { display: 'flex', flexWrap: 'wrap', gap: tokens.spacingHorizontalS, '& button': mobileTouchTarget },
  payload: { whiteSpace: 'pre-wrap', overflowWrap: 'anywhere', maxHeight: '25rem', overflowY: 'auto' },
})
