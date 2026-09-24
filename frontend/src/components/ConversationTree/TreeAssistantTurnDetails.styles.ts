import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTargetHeight } from '@/styles/touchTargets'

export const useTreeAssistantTurnDetailsStyles = makeStyles({
  root: {
    minWidth: 0,
    maxWidth: '100%',
    color: tokens.colorNeutralForeground2,
    fontSize: tokens.fontSizeBase200,
    overflowWrap: 'anywhere',
  },
  summary: {
    cursor: 'pointer',
    padding: tokens.spacingHorizontalS,
    fontWeight: tokens.fontWeightSemibold,
    ...mobileTouchTargetHeight,
    '&:focus-visible': {
      outline: `2px solid ${tokens.colorStrokeFocus2}`,
      outlineOffset: tokens.strokeWidthThin,
    },
  },
  body: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalM,
    minWidth: 0,
    maxHeight: 'min(36rem, 65dvh)',
    overflowY: 'auto',
    padding: tokens.spacingHorizontalS,
    color: tokens.colorNeutralForeground1,
    borderLeft: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  tool: {
    flexShrink: 0,
    minWidth: 0,
    border: `1px solid ${tokens.colorNeutralStroke2}`,
    borderRadius: tokens.borderRadiusSmall,
  },
  section: {
    minWidth: 0,
    flexShrink: 0,
  },
  heading: {
    marginTop: 0,
    marginBottom: tokens.spacingVerticalXS,
    fontSize: tokens.fontSizeBase200,
    fontWeight: tokens.fontWeightSemibold,
  },
  fields: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 2fr)',
    gap: `${tokens.spacingVerticalXS} ${tokens.spacingHorizontalS}`,
    marginTop: 0,
    marginBottom: tokens.spacingVerticalS,
    '& dt': { color: tokens.colorNeutralForeground2 },
    '& dd': { margin: 0, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' },
  },
  text: {
    margin: 0,
    maxHeight: '16rem',
    overflow: 'auto',
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    padding: tokens.spacingHorizontalS,
    backgroundColor: tokens.colorNeutralBackground1,
    borderRadius: tokens.borderRadiusSmall,
  },
  muted: {
    margin: 0,
    color: tokens.colorNeutralForeground2,
  },
  tools: {
    margin: 0,
    paddingLeft: tokens.spacingHorizontalL,
  },
})
