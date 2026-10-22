import { makeStyles, tokens } from '@fluentui/react-components'

export const useAgentActivityStyles = makeStyles({
  status: { padding: tokens.spacingVerticalS, display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS },
  controls: { display: 'flex', alignItems: 'center', flexWrap: 'wrap', gap: tokens.spacingHorizontalS },
  root: {
    marginInlineStart: tokens.spacingHorizontalXXL,
    padding: tokens.spacingVerticalM, borderLeft: `2px solid ${tokens.colorNeutralStroke2}`,
    display: 'flex', flexDirection: 'column', gap: tokens.spacingVerticalS,
  },
  tool: { padding: tokens.spacingVerticalS, backgroundColor: tokens.colorNeutralBackground2 },
  raw: {
    whiteSpace: 'pre-wrap', overflowWrap: 'anywhere',
    fontFamily: tokens.fontFamilyMonospace, maxHeight: '20rem', overflowY: 'auto',
  },
})
