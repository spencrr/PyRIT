import { makeStyles, tokens } from '@fluentui/react-components'

import { mobileTouchTarget, mobileTouchTargetHeight, NARROW_VIEWPORT_QUERY } from '@/styles/touchTargets'

export const useTreeForkPathDialogStyles = makeStyles({
  surface: {
    width: `min(48rem, calc(100vw - ${tokens.spacingHorizontalL} - ${tokens.spacingHorizontalL}))`,
    maxWidth: '48rem',
    [NARROW_VIEWPORT_QUERY]: {
      width: `calc(100vw - ${tokens.spacingHorizontalL} - ${tokens.spacingHorizontalL})`,
    },
  },
  content: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalL,
    minWidth: 0,
  },
  selectors: {
    display: 'grid',
    gridTemplateColumns: 'minmax(0, 1fr) minmax(0, 1fr)',
    gap: tokens.spacingHorizontalM,
    [NARROW_VIEWPORT_QUERY]: {
      gridTemplateColumns: 'minmax(0, 1fr)',
    },
  },
  input: {
    ...mobileTouchTargetHeight,
  },
  button: {
    ...mobileTouchTarget,
  },
  actions: {
    flexWrap: 'wrap',
  },
  section: {
    display: 'flex',
    flexDirection: 'column',
    gap: tokens.spacingVerticalS,
  },
  preview: {
    margin: 0,
    paddingLeft: tokens.spacingHorizontalXXL,
  },
  step: {
    padding: tokens.spacingVerticalS,
    borderBottom: `1px solid ${tokens.colorNeutralStroke2}`,
  },
  prompt: {
    margin: 0,
    whiteSpace: 'pre-wrap',
    overflowWrap: 'anywhere',
    fontFamily: tokens.fontFamilyBase,
    fontSize: tokens.fontSizeBase300,
  },
  muted: {
    color: tokens.colorNeutralForeground3,
    overflowWrap: 'anywhere',
  },
})
